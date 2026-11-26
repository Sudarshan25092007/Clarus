"""Phase 2 unit tests and normalization coverage summary across dev/input."""
from __future__ import annotations

import sys
import unittest
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter.identity import (  # noqa: E402
    NormalizedIdentity,
    RecordIdentity,
    devanagari_to_latin,
    extract_identity,
    normalize_dob,
    normalize_gender,
    normalize_identity,
    normalize_name,
    normalize_phone,
    phonetic_skeleton,
)
from tests.test_phase1_extract import _iter_dev_records  # noqa: E402


class Phase2NormalizeTests(unittest.TestCase):
    def test_devanagari_digits_and_phone(self) -> None:
        rec = extract_identity(
            "discharge/arogya_ds_ADM000170.txt",
            "doc",
            {},
            "मरीज़ का नाम: मनोज यादव    आयु: 65 वर्ष / पुरुष\nमोबाइल: ८९२१६७८५४१",
        )
        norm = normalize_identity(rec)
        self.assertIsInstance(norm, NormalizedIdentity)
        self.assertIn("8921678541", norm.norm_phones)
        self.assertEqual(norm.age_approx, 65)
        self.assertEqual(norm.gender, "M")
        self.assertEqual(normalize_phone("060062-72163"), "6006272163")
        self.assertEqual(normalize_phone("+91 72864 90658"), "7286490658")

    def test_cross_script_transliteration_and_skeletons(self) -> None:
        self.assertEqual(devanagari_to_latin("मोहम्मद आरिफ"), "mohammad arif")
        variants = ("मोहम्मद आरिफ", "Md. Arif", "Mohammad Arif")
        skels = [phonetic_skeleton(normalize_name(v)) for v in variants]
        self.assertEqual(len(set(skels)), 1)
        self.assertEqual(skels[0], "mhmd arf")

        # Additional real dev/input cross-script & spelling equivalents
        self.assertEqual(
            phonetic_skeleton(normalize_name("अनीता शर्मा")),
            phonetic_skeleton(normalize_name("Aneeta Sarma")),
        )
        self.assertEqual(
            phonetic_skeleton(normalize_name("फ़ातिमा क़ुरैशी")),
            phonetic_skeleton(normalize_name("Fatma Kureshi")),
        )
        self.assertEqual(
            phonetic_skeleton(normalize_name("कमला निषाद")),
            phonetic_skeleton(normalize_name("Kamla Nishad")),
        )
        self.assertEqual(
            phonetic_skeleton(normalize_name("पार्वती पांडे")),
            phonetic_skeleton(normalize_name("Parvathi Pandeya")),
        )
        self.assertEqual(
            phonetic_skeleton(normalize_name("नरेश चौधरी")),
            phonetic_skeleton(normalize_name("Naresh Chaudhary")),
        )
        self.assertEqual(
            phonetic_skeleton(normalize_name("सरिता तिवारी")),
            phonetic_skeleton(normalize_name("Sarita Tiwary")),
        )

    def test_honorific_and_title_stripping(self) -> None:
        self.assertEqual(normalize_name("Shahid sahab"), "shahid")
        self.assertEqual(normalize_name("Shahid babu"), "shahid")
        self.assertEqual(normalize_name("Sunita amma"), "sunita")
        self.assertEqual(normalize_name("Asha ji"), "asha")
        self.assertEqual(normalize_name("Kamal Bhaiya"), "kamal")
        self.assertEqual(normalize_name("Smt. Geeta Devi"), "geeta")
        self.assertEqual(normalize_name("Shri Ramesh Kumar"), "ramesh kumar")
        self.assertEqual(normalize_name("Kumari Sunita"), "sunita")
        self.assertEqual(normalize_name("Mohd. Shahid"), "mohammad shahid")

    def test_gender_and_age_sex_normalization(self) -> None:
        self.assertEqual(normalize_gender("महिला"), "F")
        self.assertEqual(normalize_gender("पुरुष"), "M")
        self.assertEqual(normalize_gender("Female"), "F")
        self.assertEqual(normalize_gender("Male"), "M")

        rec = extract_identity(
            "lab/city_results.csv",
            "row:0",
            {"patient_name": "Rina Nishad", "age_sex": "65/F", "uhid": "JJH-0076928"},
        )
        norm = normalize_identity(rec)
        self.assertEqual(norm.age_approx, 65)
        self.assertEqual(norm.gender, "F")
        self.assertIn(("mrn", "JJH-0076928"), norm.strong_ids)

    def test_strong_ids_masked_aadhaar_and_dob(self) -> None:
        rec = RecordIdentity(
            relpath="registration/sanjeevani_registration.csv",
            loc="row:1",
            hospital_hint="sanjeevani",
            names=["Shanti Chaudhary"],
            mrns=["SNJ/26/064579"],
            aadhaar=["XXXX XXXX 5866", "3972 0583 9628"],
            abha=["91-7938-3729-8403"],
            pan=["dcsps3454e"],
            insurance_ids=["PMJAY-UP-3398744800"],
            dob="19/02/1976",
            phones=["060062-72163"],
        )
        norm = normalize_identity(rec)
        self.assertEqual(norm.norm_phones, ["6006272163"])
        self.assertEqual(norm.dob_iso, "1976-02-19")
        self.assertIn(("mrn", "SNJ/26/064579"), norm.strong_ids)
        self.assertIn(("aadhaar", "397205839628"), norm.strong_ids)
        self.assertNotIn(("aadhaar", "5866"), norm.strong_ids)
        self.assertFalse(any(t == "aadhaar" and "5866" in v and len(v) < 12 for t, v in norm.strong_ids))
        self.assertIn(("abha", "91793837298403"), norm.strong_ids)
        self.assertIn(("pan", "DCSPS3454E"), norm.strong_ids)
        self.assertIn(("insurance", "PMJAYUP3398744800"), norm.strong_ids)

        # Hospital-specific date format hints (Arogya MM/DD/YYYY vs Sanjeevani DD/MM/YYYY)
        self.assertEqual(normalize_dob("05/30/1986", "arogya"), "1986-05-30")
        self.assertEqual(normalize_dob("12/04/1977", "sanjeevani"), "1977-04-12")
        self.assertEqual(normalize_dob("1998-10-19", "sanjeevani"), "1998-10-19")

    def test_clinical_trap_safety(self) -> None:
        rec = RecordIdentity(
            relpath="clean/dengue_management_protocol.txt",
            loc="doc",
            names=["C/O fever", "K/C/O addison's disease"],
            relatives=[("C/o", "fever"), ("K/C/O", "addison's disease")],
        )
        norm = normalize_identity(rec)
        self.assertEqual(norm.norm_names, [])
        self.assertEqual(norm.norm_relatives, [])


def run_normalization_summary() -> None:
    dev_input = REPO_ROOT / "dev" / "input"
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for item in _iter_dev_records(dev_input):
        if item[0] == "FAIL":
            continue
        _, sub, rel, loc, raw_fields, raw_text = item
        rec = extract_identity(rel, loc, raw_fields, raw_text)
        norm = normalize_identity(rec)
        s = stats[sub]
        s["records"] += 1
        s["norm_names"] += int(bool(norm.norm_names))
        s["skeletons"] += int(bool(norm.name_skeletons))
        s["phones"] += int(bool(norm.norm_phones))
        s["strong_ids"] += int(bool(norm.strong_ids))
        s["dob_or_gender"] += int(norm.dob_iso is not None or norm.gender is not None)
        s["dob"] += int(norm.dob_iso is not None)
        s["gender"] += int(norm.gender is not None)
        s["relatives"] += int(bool(norm.norm_relatives))

    totals: dict[str, int] = defaultdict(int)
    print("\n=== Phase 2 Normalization Summary (dev/input/) ===")
    hdr = (
        f"{'Subfolder':<14} {'Recs':>5} {'Names':>6} {'Skels':>6} {'Phone':>6} "
        f"{'StrID':>6} {'DOB/Gen':>8} {'Rel':>5}"
    )
    print(hdr)
    print("-" * len(hdr))
    for sub in sorted(stats):
        s = stats[sub]
        for k, v in s.items():
            totals[k] += v
        print(
            f"{sub:<14} {s['records']:>5} {s['norm_names']:>6} {s['skeletons']:>6} "
            f"{s['phones']:>6} {s['strong_ids']:>6} {s['dob_or_gender']:>8} {s['relatives']:>5}"
        )
    print("-" * len(hdr))
    print(
        f"{'TOTAL':<14} {totals['records']:>5} {totals['norm_names']:>6} {totals['skeletons']:>6} "
        f"{totals['phones']:>6} {totals['strong_ids']:>6} {totals['dob_or_gender']:>8} {totals['relatives']:>5}"
    )


if __name__ == "__main__":
    run_normalization_summary()
    unittest.main(verbosity=2)
