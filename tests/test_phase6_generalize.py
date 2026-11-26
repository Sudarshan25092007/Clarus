"""Phase 6 unit tests for unseen-hospital generalization, MRN equivalence, and guarded Pass-2 recall."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter.identity import (  # noqa: E402
    IdentityResolver,
    extract_identity,
    normalize_dob,
    normalize_identity,
    phonetic_skeleton,
)


class Phase6GeneralizeTests(unittest.TestCase):
    def test_unseen_hospital_headers_dates_and_generic_mrns(self) -> None:
        # 1. Textual month & explicit MDY dates
        self.assertEqual(normalize_dob("12 Apr 1977"), "1977-04-12")
        self.assertEqual(normalize_dob("5 September 2026"), "2026-09-05")
        self.assertEqual(normalize_dob("03/08/1986", date_order="MDY"), "1986-03-08")
        self.assertEqual(normalize_dob("03/08/1986"), "1986-08-03")

        # 2. Unseen facility ("City Care Hospital") with CCH/27/... MRN, DOB (MM/DD/YYYY),
        # unseen columns ("Patient's Name", "Gram/Mohalla", "Contact No", "Father's Name"),
        # and ambiguous date 03/08/1986 across CSV, JSON metadata, and pipe-delimited note.
        resolver = IdentityResolver()
        resolver.add_record(
            "reg/citycare_patients.csv",
            "row:0",
            {
                "Patient's Name": "Meera Verma",
                "MR Number": "CCH/27/00123",
                "DOB (MM/DD/YYYY)": "03/08/1986",
                "Gender": "F",
                "Gram/Mohalla": "Sitapurwa",
                "Contact No": "9811122334",
                "Father's Name": "Harpal Verma",
            },
        )
        resolver.add_record(
            "opd/city_care_opd.json",
            "/visits/0",
            {
                "_export_meta": {"hospital": "City Care Hospital"},
                "patient": {"name": "Meera Verma", "mrn": "CCH/27/00123", "birthDate": "1986-03-08"},
            },
        )
        resolver.add_record(
            "notes/cch_ward.txt",
            "doc",
            {},
            "CITY CARE HOSPITAL\nPatient's Name: Meera Verma | DOB: 03/08/1986 | MRN: CCH/27/00123",
        )
        # Bare numeric MRNs from two unrelated files with unknown hospitals must NOT collide.
        resolver.add_record(
            "unknown_alpha/reg_1.csv",
            "row:0",
            {"patient_name": "Kiran Rao", "mrn": "004512", "gender": "F"},
        )
        resolver.add_record(
            "unknown_beta/reg_2.csv",
            "row:0",
            {"patient_name": "Kiran Rao", "mrn": "004512", "gender": "F"},
        )
        resolver.resolve_all()

        pids = {
            resolver.get_patient_id("reg/citycare_patients.csv", "row:0"),
            resolver.get_patient_id("opd/city_care_opd.json", "/visits/0"),
            resolver.get_patient_id("notes/cch_ward.txt", "doc"),
        }
        self.assertEqual(len(pids), 1)
        self.assertIsNotNone(next(iter(pids)))
        self.assertEqual(
            resolver._by_locator[("reg/citycare_patients.csv", "row:0")].hospital_hint,
            "citycare",
        )
        self.assertEqual(
            resolver._by_locator[("opd/city_care_opd.json", "/visits/0")].hospital_hint,
            "citycare",
        )
        self.assertIsNone(resolver.get_patient_id("unknown_alpha/reg_1.csv", "row:0"))
        self.assertIsNone(resolver.get_patient_id("unknown_beta/reg_2.csv", "row:0"))

    def test_file_level_column_mdy_inference_and_non_mrn_filters(self) -> None:
        # Unseen CSV without format hint in column name, where row:1 proves MDY -> row:0 resolves MDY too.
        resolver = IdentityResolver()
        resolver.add_record(
            "unseen/reg.csv",
            "row:0",
            {"name": "Anil Tiwari", "dob": "03/08/1986", "mrn": "KGMU-98412", "phone": "9812345670"},
        )
        resolver.add_record(
            "unseen/reg.csv",
            "row:1",
            {"name": "Pooja Devi", "dob": "03/25/1990", "mrn": "KGMU-98413", "phone": "9812345671"},
        )
        resolver.add_record(
            "unseen/iso.json",
            "doc",
            {"name": "Anil Tiwari", "dob": "1986-03-08", "mrn": "KGMU-98412", "phone": "9812345670"},
        )
        resolver.resolve_all()
        self.assertEqual(
            resolver.get_patient_id("unseen/reg.csv", "row:0"),
            resolver.get_patient_id("unseen/iso.json", "doc"),
        )

        # Generic inline MRN extractor captures CCH/27/00123 but skips ICD-10, BL/26/..., OPD/26/..., AMB-410.
        txt_rec = extract_identity(
            "notes/summary.txt",
            "doc",
            {},
            "MRN: CCH/27/00123 | Bill BL/26/000235 | Visit OPD/26/02/00085 | ICD-10 E11 | AMB-410\n"
            "सीता देवी, पत्नी रामलाल\nC/O Parkinson's disease",
        )
        norm = normalize_identity(txt_rec)
        self.assertIn(("mrn", "CCH/27/00123"), norm.strong_ids)
        for bad in ("BL/26/000235", "OPD/26/02/00085", "ICD-10", "AMB-410"):
            self.assertNotIn(("mrn", bad), norm.strong_ids)
        self.assertTrue(
            any(
                rel == "W/o" and phonetic_skeleton(name) == phonetic_skeleton("ramlal")
                for rel, name in norm.norm_relatives
            )
        )
        self.assertFalse(any("parkinson" in n for n in norm.norm_names))

    def test_verified_mrn_equivalence_without_false_merges(self) -> None:
        resolver = IdentityResolver()
        # Re-registered patient with two same-series MRNs proven via Aadhaar + name + DOB.
        resolver.add_record(
            "kgmu/reg.csv",
            "row:0",
            {
                "hospital": "KGMU Lucknow",
                "patient_name": "Rohan Kapoor",
                "mrn": "KGMU-10001",
                "aadhaar": "555566667777",
                "dob": "1990-02-14",
                "gender": "M",
            },
        )
        resolver.add_record(
            "kgmu/reg.csv",
            "row:1",
            {
                "hospital": "KGMU Lucknow",
                "patient_name": "Rohan Kapoor",
                "mrn": "KGMU-10089",
                "aadhaar": "555566667777",
                "dob": "1990-02-14",
                "gender": "M",
            },
        )
        # Downstream lab row carrying only KGMU-10089 (no Aadhaar/DOB).
        resolver.add_record(
            "kgmu/lab.csv",
            "row:0",
            {"hospital": "KGMU Lucknow", "patient_name": "Rohan Kapoor", "mrn": "KGMU-10089"},
        )
        # Unrelated patient at same hospital sharing name but different MRN and Aadhaar/DOB.
        resolver.add_record(
            "kgmu/reg.csv",
            "row:2",
            {
                "hospital": "KGMU Lucknow",
                "patient_name": "Rohan Kapoor",
                "mrn": "KGMU-20999",
                "aadhaar": "111122223333",
                "dob": "1975-09-01",
                "gender": "M",
            },
        )
        resolver.resolve_all()

        p0 = resolver.get_patient_id("kgmu/reg.csv", "row:0")
        p1 = resolver.get_patient_id("kgmu/reg.csv", "row:1")
        pl = resolver.get_patient_id("kgmu/lab.csv", "row:0")
        p_other = resolver.get_patient_id("kgmu/reg.csv", "row:2")
        self.assertIsNotNone(p0)
        self.assertEqual(p0, p1)
        self.assertEqual(p0, pl)
        self.assertNotEqual(p0, p_other)

    def test_uniqueness_gated_second_pass_refuses_ambiguous_homonyms(self) -> None:
        # 1. Ambiguous homonym (2 competing clusters share full name + village + age) -> refuse!
        ambig = IdentityResolver()
        ambig.add_record(
            "reg/a.csv",
            "row:0",
            {
                "patient_name": "Rajesh Yadav",
                "uhid": "CCH/27/00001",
                "dob": "1984-01-10",
                "age": "42",
                "village": "Bhimpur",
            },
        )
        ambig.add_record(
            "reg/b.csv",
            "row:0",
            {
                "patient_name": "Rajesh Yadav",
                "uhid": "CCH/27/00002",
                "dob": "1984-05-12",
                "age": "42",
                "village": "Bhimpur",
            },
        )
        ambig.add_record(
            "rx/sparse.txt",
            "doc",
            {},
            "नाम: राजेश यादव   आयु: 42 वर्ष\nग्राम: भीमपुर",
        )
        ambig.resolve_all()
        self.assertIsNone(ambig.get_patient_id("rx/sparse.txt", "doc"))

        # 2. Unique (full_name + village + age <= 2) AND unique (partial_name + relative + Visit date) -> link!
        uniq = IdentityResolver()
        uniq.add_record(
            "reg/u.csv",
            "row:0",
            {
                "patient_name": "Kamlesh Tiwari",
                "uhid": "CCH/27/00010",
                "dob": "1978-06-01",
                "age": "48",
                "village": "Ramnagar",
            },
        )
        uniq.add_record(
            "rx/u.txt",
            "doc",
            {},
            "नाम: कमलेश तिवारी   आयु: 48 वर्ष\nग्राम: रामनगर",
        )
        uniq.add_record(
            "reg/s.csv",
            "row:0",
            {
                "patient_name": "Sunita Devi",
                "uhid": "CCH/27/00011",
                "relation": "W/o",
                "relative_name": "Mohan Kumar",
                "discharge_date": "2026-09-05",
            },
        )
        uniq.add_record(
            "reg/s_other.csv",
            "row:0",
            {
                "patient_name": "Sunita Verma",
                "uhid": "CCH/27/00012",
                "relation": "W/o",
                "relative_name": "Ramesh Kumar",
            },
        )
        uniq.add_record(
            "discharge/s_frag.txt",
            "doc",
            {},
            "Discharge note\n\nSunita, W/o Mohan, discharged in stable condition.\nVisit: 5 September",
        )
        uniq.resolve_all()
        self.assertEqual(
            uniq.get_patient_id("rx/u.txt", "doc"),
            uniq.get_patient_id("reg/u.csv", "row:0"),
        )
        self.assertEqual(
            uniq.get_patient_id("discharge/s_frag.txt", "doc"),
            uniq.get_patient_id("reg/s.csv", "row:0"),
        )
        self.assertNotEqual(
            uniq.get_patient_id("discharge/s_frag.txt", "doc"),
            uniq.get_patient_id("reg/s_other.csv", "row:0"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
