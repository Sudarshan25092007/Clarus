"""Final hardening tests for k-anonymity, generalized quarantine, config, and stdout safety."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter import main as starter_main  # noqa: E402


class FinalHardeningTests(unittest.TestCase):
    def _run_pipeline(self, inp_dir: Path, out_dir: Path, secret: str = "practice") -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, RAKSHA_SECRET=secret)
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "starter" / "main.py"),
                "--input",
                str(inp_dir),
                "--output",
                str(out_dir),
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_k_anonymity_large_intermediate_and_rare_cohorts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clarus-kanon-") as tmp:
            inp = Path(tmp) / "inp"
            out = Path(tmp) / "out"
            inp.mkdir()

            # Cohort A (6 distinct patients in Lucknow / Bakshi / M / 40-44): >= 5 -> kept intact
            # Cohort B (3 in Kanpur/Ghatampur/F/32 + 3 in Kanpur/Bilhaur/F/33):
            #   drops tehsil -> combined (Kanpur, [SUPPRESSED], F, 30-34) = 6 >= 5 -> 5-yr band preserved!
            # Cohort C (1 rare patient in Sitapur / Biswan / M / 39): < 5 even without tehsil ->
            #   tehsil=[SUPPRESSED] AND age widens to 30-39 (10-yr)!
            rows = [
                ["UHID", "Patient Name", "Gender", "Age", "Tehsil", "District", "Mobile", "Diagnosis", "Medication"],
            ]
            for i in range(6):
                rows.append([f"LKO/{i}", f"Patient LKO {i}", "M", "42", "Bakshi", "Lucknow", f"980000010{i}", "J06.9 Acute URI", "Paracetamol 500 mg"])
            for i in range(3):
                rows.append([f"KNP/A/{i}", f"Patient KNP A{i}", "F", "32", "Ghatampur", "Kanpur", f"980000020{i}", "R50.9 Fever", "Azithromycin 500 mg"])
            for i in range(3):
                rows.append([f"KNP/B/{i}", f"Patient KNP B{i}", "F", "33", "Bilhaur", "Kanpur", f"980000030{i}", "R51 Headache", "Ibuprofen 400 mg"])
            rows.append(["STP/0", "Patient STP Rare", "M", "39", "Biswan", "Sitapur", "9800000400", "A90 Dengue", "Dolo 650 mg"])

            buf = io.StringIO()
            csv.writer(buf, lineterminator="\n").writerows(rows)
            (inp / "cohort_reg.csv").write_text(buf.getvalue(), encoding="utf-8")

            # Also include structured JSON record for the rare Sitapur patient to verify JSON suppression
            jdoc = {
                "patients": [
                    {
                        "uhid": "STP/0",
                        "name": {"first": "Patient", "last": "STP Rare", "local": ""},
                        "gender": "M",
                        "age": 39,
                        "address": {"line": "H.No 1", "village": "Kalan", "tehsil": "Biswan", "district": "Sitapur", "pin": "261167"},
                    }
                ]
            }
            json_path = inp / "cohort_records.json"
            json_path.write_text(json.dumps(jdoc), encoding="utf-8")

            proc = self._run_pipeline(inp, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            sha_to_out = {
                e["input_sha256"]: e["output"]
                for e in report["files"]
                if e.get("status") == "sanitized" and e.get("output")
            }
            import hashlib
            csv_out_rel = sha_to_out[hashlib.sha256((inp / "cohort_reg.csv").read_bytes()).hexdigest()]
            json_out_rel = sha_to_out[hashlib.sha256(json_path.read_bytes()).hexdigest()]
            out_csv = list(csv.reader(io.StringIO((out / csv_out_rel).read_text(encoding="utf-8"), newline="")))
            body = out_csv[1:]

            # Cohort A (rows 0..5): >= 5 -> tehsil intact, 5-year band 40-44
            for r in body[:6]:
                self.assertEqual(r[3], "40-44")
                self.assertEqual(r[4], "Bakshi")
                self.assertEqual(r[5], "Lucknow")
                self.assertEqual(r[7], "J06.9 Acute URI")
                self.assertEqual(r[8], "Paracetamol 500 mg")

            # Cohort B (rows 6..11): tehsil suppressed, 5-yr age band 30-34 kept because pooled >= 5
            for r in body[6:12]:
                self.assertEqual(r[3], "30-34")
                self.assertEqual(r[4], "[SUPPRESSED]")
                self.assertEqual(r[5], "Kanpur")

            # Cohort C (row 12): rare cohort -> tehsil suppressed AND 10-yr age band 30-39; medical preserved
            rare = body[12]
            self.assertEqual(rare[3], "30-39")
            self.assertEqual(rare[4], "[SUPPRESSED]")
            self.assertEqual(rare[5], "Sitapur")
            self.assertEqual(rare[7], "A90 Dengue")
            self.assertEqual(rare[8], "Dolo 650 mg")

            out_json = json.loads((out / json_out_rel).read_text(encoding="utf-8"))
            p0 = out_json["patients"][0]
            self.assertEqual(p0["age"], "30-39")
            self.assertEqual(p0["address"]["tehsil"], "[SUPPRESSED]")
            self.assertEqual(p0["address"]["district"], "Sitapur")

            # Truthful count: 6 Cohort B + 1 Cohort C CSV + 1 Cohort C JSON = 8 suppressed records (6 Cohort A untouched)
            self.assertEqual(report["counts"]["records_suppressed"], 8)

    def test_generalized_quarantine_structural_checks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="clarus-q-") as tmp:
            inp = Path(tmp) / "inp"
            out = Path(tmp) / "out"
            inp.mkdir()

            # 1. Generic deep JSON with non-"level" keys (depth 70 > 64)
            deep_s = '{"a":' * 70 + "1" + "}" * 70
            (inp / "deep_custom.json").write_text(deep_s, encoding="utf-8")

            # 2. Genuine cp1252 CSV (must NOT be quarantined)
            (inp / "valid_cp1252.csv").write_bytes(b"ID,Note\n1,Temp 98.6\xb0F \x96 normal\n")

            # 3. Null byte & truncated UTF-8 corrupt text
            (inp / "nul_bad.txt").write_bytes(b"OPD note\x00binary garbage\n")
            (inp / "broken_utf8.txt").write_bytes(b"Patient note \xe0\xa4 broken bytes\n")

            # 4. Mislabeled CSV containing JSON array
            (inp / "array_in_csv.tsv").write_text('[{"ok": 1}]\n', encoding="utf-8")

            proc = self._run_pipeline(inp, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            by_sha = {e["input_sha256"]: e for e in report["files"]}

            import hashlib
            def status_of(fname: str) -> dict:
                sha = hashlib.sha256((inp / fname).read_bytes()).hexdigest()
                return by_sha[sha]

            self.assertEqual(status_of("deep_custom.json")["status"], "quarantined")
            self.assertEqual(status_of("deep_custom.json")["reason"], "excessive_depth")
            self.assertEqual(status_of("valid_cp1252.csv")["status"], "sanitized")
            self.assertEqual(status_of("nul_bad.txt")["status"], "quarantined")
            self.assertEqual(status_of("nul_bad.txt")["reason"], "corrupt_bytes")
            self.assertEqual(status_of("broken_utf8.txt")["status"], "quarantined")
            self.assertEqual(status_of("broken_utf8.txt")["reason"], "corrupt_bytes")
            self.assertEqual(status_of("array_in_csv.tsv")["status"], "quarantined")
            self.assertEqual(status_of("array_in_csv.tsv")["reason"], "mislabeled_format")

    def test_stdout_stderr_zero_pii_and_no_secret(self) -> None:
        secret = "ultra-secret-canary-key-9182"
        with tempfile.TemporaryDirectory(prefix="clarus-stdout-") as tmp:
            out = Path(tmp) / "out"
            proc = self._run_pipeline(REPO_ROOT / "dev" / "input", out, secret=secret)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stderr, "")
            self.assertRegex(
                proc.stdout.strip(),
                r"^files \d+ sanitized \d+ quarantined \d+ records \d+$",
            )
            combined = proc.stdout + "\n" + proc.stderr
            for sensitive in (
                secret,
                "Mohammad",
                "Shahid",
                "9368591915",
                "SNJ/26/064258",
                "Zubair",
                "Mukesh",
            ):
                self.assertNotIn(sensitive, combined)

    def test_config_and_raksha_toml(self) -> None:
        toml_txt = (REPO_ROOT / "starter" / "raksha.toml").read_text(encoding="utf-8")
        self.assertIn('name  = "clarus"', toml_txt)
        pol = starter_main.load_policy(REPO_ROOT / "starter" / "config" / "policy.yaml")
        self.assertEqual(pol["min_group_size"], 5)
        fallback_pol = starter_main.load_policy(REPO_ROOT / "nonexistent_policy_file.yaml")
        self.assertEqual(fallback_pol["min_group_size"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
