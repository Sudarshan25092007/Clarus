"""Phase 5 tests for per-patient date shifting, interval preservation, and age_band()."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import sys
import unittest
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "starter") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "starter"))

from starter import main as starter_main  # noqa: E402
from starter.identity import IdentityResolver  # noqa: E402
from tests.test_phase1_extract import _iter_dev_records  # noqa: E402

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _make_hmac_helpers(secret: bytes, max_days: int = 180) -> tuple[Callable[[str], str], Callable[[str], int]]:
    def _pseudonym(patient_key: str) -> str:
        digest = hmac.new(secret, patient_key.encode("utf-8"), hashlib.sha256).digest()
        n = int.from_bytes(digest[:5], "big")
        code = "".join(_ALPHABET[(n >> (5 * i)) & 31] for i in range(6))
        return f"PSN-{code}"

    def _shift_days(patient_key: str) -> int:
        d = hmac.new(secret, b"shift|" + patient_key.encode("utf-8"), hashlib.sha256).digest()
        v = int.from_bytes(d[:4], "big") % (2 * max_days) - max_days
        return v if v != 0 else 1

    return _pseudonym, _shift_days


class Phase5TemporalTests(unittest.TestCase):
    def test_cross_hospital_same_shift_and_interval_preservation(self) -> None:
        dev_input = REPO_ROOT / "dev" / "input"
        psn_fn, shift_fn = _make_hmac_helpers(b"practice")
        resolver = IdentityResolver(pseudonym_fn=psn_fn, date_shift_fn=shift_fn)

        for item in _iter_dev_records(dev_input):
            if item[0] == "FAIL":
                continue
            _, _, rel, loc, raw_fields, raw_text = item
            resolver.add_record(rel, loc, raw_fields, raw_text)
        resolver.resolve_all()

        # PT00015 (Mohammad Shahid) spans Arogya + Sanjeevani + Discharge + Notes + Lab + Billing
        locs = [
            ("registration/arogya_legacy_export.csv", "row:5"),
            ("registration/sanjeevani_registration.csv", "row:17"),
            ("discharge/Discharge_Summary_Shahid_Mohammad_UHID-010607.txt", "doc"),
            ("notes/nursing_adm000161.txt", "doc"),
            ("lab/9368591915_report_0032.json", "doc"),
            ("billing/arogya_claims.csv", "row:0"),
        ]
        offsets = [resolver.get_date_shift_days(r, l) for r, l in locs]
        self.assertTrue(all(o is not None for o in offsets))
        self.assertEqual(len(set(offsets)), 1)
        offset = offsets[0]
        assert offset is not None
        self.assertNotEqual(offset, 0)
        self.assertTrue(-180 <= offset <= 180)

        # Length-of-stay and cross-event intervals must remain exact after shifting.
        admit = dt.date(2026, 1, 13)
        discharge = dt.date(2026, 1, 18)
        visit = dt.date(2026, 3, 23)
        lab_date = dt.date(2026, 10, 22)

        s_admit = resolver.shift_patient_date(locs[2][0], locs[2][1], admit)
        s_discharge = resolver.shift_patient_date(locs[5][0], locs[5][1], discharge)
        s_visit = resolver.shift_patient_date(locs[0][0], locs[0][1], visit)
        s_lab = resolver.shift_patient_date(locs[4][0], locs[4][1], lab_date)

        self.assertEqual((s_discharge - s_admit).days, (discharge - admit).days)
        self.assertEqual((s_lab - s_visit).days, (lab_date - visit).days)

        # Non-patient clean/misc records return None for get_date_shift_days and keep date unchanged.
        self.assertIsNone(resolver.get_date_shift_days("clean/dengue_management_protocol.txt", "doc"))
        self.assertIsNone(resolver.get_date_shift_days("clean/drug_formulary.csv", "row:0"))
        self.assertIsNone(resolver.get_date_shift_days("misc/README_export.txt", "doc"))
        self.assertIsNone(resolver.get_date_shift_days("misc/ambulance_log.csv", "row:0"))
        self.assertEqual(
            resolver.shift_patient_date("clean/dengue_management_protocol.txt", "doc", admit),
            admit,
        )

    def test_different_secrets_produce_different_shifts(self) -> None:
        psn_a, shift_a = _make_hmac_helpers(b"secret-one")
        psn_b, shift_b = _make_hmac_helpers(b"secret-two")

        res_a = IdentityResolver(pseudonym_fn=psn_a, date_shift_fn=shift_a)
        res_b = IdentityResolver(pseudonym_fn=psn_b, date_shift_fn=shift_b)
        for res in (res_a, res_b):
            res.add_record(
                "reg/a.csv",
                "row:0",
                {"patient_name": "Mahesh Chauhan", "uhid": "SNJ/26/064127", "phone": "7286490658"},
            )
            res.add_record(
                "reg/b.csv",
                "row:0",
                {"patient_name": "Anita Sharma", "uhid": "UHID-010565", "phone": "7585697553"},
            )
            res.resolve_all()

        shifts_a = (
            res_a.get_date_shift_days("reg/a.csv", "row:0"),
            res_a.get_date_shift_days("reg/b.csv", "row:0"),
        )
        shifts_b = (
            res_b.get_date_shift_days("reg/a.csv", "row:0"),
            res_b.get_date_shift_days("reg/b.csv", "row:0"),
        )
        self.assertNotEqual(shifts_a, shifts_b)

    def test_age_band_and_starter_shift_date_compatibility(self) -> None:
        self.assertEqual(starter_main.age_band(47), "45-49")
        self.assertEqual(starter_main.age_band(0), "0-4")
        self.assertEqual(starter_main.age_band(50), "50-54")
        for age in range(0, 105):
            band = starter_main.age_band(age)
            lo_s, hi_s = band.split("-")
            self.assertLessEqual(int(lo_s), age)
            self.assertLessEqual(age, int(hi_s))

        # Verify starter/main.py shift_date() works directly with resolver.get_patient_key()
        res = IdentityResolver(
            pseudonym_fn=starter_main.pseudonym,
            date_shift_fn=starter_main.date_shift_days,
        )
        res.add_record(
            "reg/sanjeevani.csv",
            "row:0",
            {"patient_name": "Mahesh Chauhan", "uhid": "SNJ/26/064127", "phone": "7286490658"},
        )
        res.resolve_all()
        pkey = res.get_patient_key("reg/sanjeevani.csv", "row:0")
        assert pkey is not None
        base = dt.date(2026, 2, 4)
        self.assertEqual(
            res.shift_patient_date("reg/sanjeevani.csv", "row:0", base),
            starter_main.shift_date(base, pkey),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
