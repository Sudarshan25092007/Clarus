"""Phase 4 unit and end-to-end tests for patient_key, HMAC pseudonyms, and records.jsonl."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter.identity import IdentityResolver  # noqa: E402

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PSN_RE = re.compile(r"^PSN-[0-9A-HJKMNP-TV-Z]{6}$")
_INTERNAL_KEY_PREFIXES = (
    "abha:",
    "aadhaar:",
    "pan:",
    "insurance:",
    "mrn:",
    "phone:",
    "demog:",
    "rec:",
    "|at:",
)


def _hmac_pseudonym(secret: bytes) -> Callable[[str], str]:
    def _fn(patient_key: str) -> str:
        digest = hmac.new(secret, patient_key.encode("utf-8"), hashlib.sha256).digest()
        n = int.from_bytes(digest[:5], "big")
        code = "".join(_ALPHABET[(n >> (5 * i)) & 31] for i in range(6))
        return f"PSN-{code}"

    return _fn


class Phase4HmacTests(unittest.TestCase):
    def test_patient_key_priority_non_patient_none_and_collision_guard(self) -> None:
        resolver = IdentityResolver(pseudonym_fn=_hmac_pseudonym(b"unit-secret-1"))
        resolver.add_record(
            "reg/a.csv",
            "row:0",
            {
                "patient_name": "Mahesh Chauhan",
                "abha": "91-7938-3729-8403",
                "aadhaar": "397205839628",
                "uhid": "SNJ/26/064127",
                "phone": "7286490658",
                "dob": "12/04/1977",
                "gender": "M",
            },
        )
        resolver.add_record(
            "clean/protocol.txt",
            "doc",
            {},
            "C/O fever for 2-7 days. Paracetamol 650 mg. Dr. Ramesh Srivastava.",
        )
        # Shared family phone with gender conflict must stay separate AND never share a PSN.
        resolver.add_record(
            "fam/m.csv",
            "row:0",
            {"patient_name": "Rohan Verma", "phone": "9876543210", "gender": "M"},
        )
        resolver.add_record(
            "fam/f.csv",
            "row:0",
            {"patient_name": "Rohan Verma", "phone": "9876543210", "gender": "F"},
        )
        resolver.resolve_all()

        key_a = resolver.get_patient_key("reg/a.csv", "row:0")
        pid_a = resolver.get_patient_id("reg/a.csv", "row:0")
        self.assertEqual(key_a, "abha:91793837298403")
        self.assertIsNotNone(pid_a)
        assert pid_a is not None
        self.assertRegex(pid_a, _PSN_RE)

        self.assertIsNone(resolver.get_patient_id("clean/protocol.txt", "doc"))
        self.assertIsNone(resolver.get_patient_key("clean/protocol.txt", "doc"))

        pid_m = resolver.get_patient_id("fam/m.csv", "row:0")
        pid_f = resolver.get_patient_id("fam/f.csv", "row:0")
        self.assertIsNotNone(pid_m)
        self.assertIsNotNone(pid_f)
        self.assertNotEqual(pid_m, pid_f)

    def test_cli_determinism_unlinkability_and_no_pii_in_metadata(self) -> None:
        dev_input = REPO_ROOT / "dev" / "input"
        main_py = REPO_ROOT / "starter" / "main.py"

        with tempfile.TemporaryDirectory(prefix="clarus-p4-") as tmp:
            tmp_path = Path(tmp)
            out_a1 = tmp_path / "out_a1"
            out_a2 = tmp_path / "out_a2"
            out_b = tmp_path / "out_b"

            for out_dir, secret in (
                (out_a1, "secret-alpha-123"),
                (out_a2, "secret-alpha-123"),
                (out_b, "secret-beta-456"),
            ):
                env = dict(os.environ, RAKSHA_SECRET=secret)
                proc = subprocess.run(
                    [sys.executable, str(main_py), "--input", str(dev_input), "--output", str(out_dir)],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, msg=proc.stderr)

            rj_a1 = (out_a1 / "records.jsonl").read_text(encoding="utf-8")
            rj_a2 = (out_a2 / "records.jsonl").read_text(encoding="utf-8")
            rj_b = (out_b / "records.jsonl").read_text(encoding="utf-8")

            # Same RAKSHA_SECRET -> byte-identical records.jsonl
            self.assertEqual(rj_a1, rj_a2)

            rows_a = [json.loads(line) for line in rj_a1.splitlines() if line.strip()]
            rows_b = [json.loads(line) for line in rj_b.splitlines() if line.strip()]
            self.assertEqual(len(rows_a), len(rows_b))
            self.assertGreater(len(rows_a), 750)

            map_a = {(r["file"], r["loc"]): r["patient"] for r in rows_a}
            map_b = {(r["file"], r["loc"]): r["patient"] for r in rows_b}
            self.assertEqual(set(map_a), set(map_b))

            non_null_a = {k: v for k, v in map_a.items() if v is not None}
            non_null_b = {k: v for k, v in map_b.items() if v is not None}
            self.assertEqual(set(non_null_a), set(non_null_b))
            self.assertGreater(len(non_null_a), 700)

            for k in non_null_a:
                self.assertRegex(non_null_a[k], _PSN_RE)
                self.assertRegex(non_null_b[k], _PSN_RE)
                self.assertNotEqual(non_null_a[k], non_null_b[k])

            # Zero internal patient_key, secret, or sample raw PII in records.jsonl or report.json
            rep_a1 = (out_a1 / "report.json").read_text(encoding="utf-8")
            combined = rj_a1 + "\n" + rep_a1
            for forbidden in (
                "secret-alpha-123",
                "Mahesh Chauhan",
                "Mohammad Shahid",
                "7286490658",
                "397205839628",
                "SNJ/26/064127",
                "JJH-0076893",
                *_INTERNAL_KEY_PREFIXES,
            ):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
