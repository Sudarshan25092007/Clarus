"""Phase 7 tests for Pass 2 pseudonym consistency, FHIR R4 integrity, date shifts, and age bands."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class Phase7Pass2WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="clarus-p7-")
        cls.out_dir = Path(cls._tmp.name) / "out"
        env = dict(os.environ, RAKSHA_SECRET="practice")
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "starter" / "main.py"),
                "--input",
                str(REPO_ROOT / "dev" / "input"),
                "--output",
                str(cls.out_dir),
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        cls.san_dir = cls.out_dir / "sanitized"
        cls.report = json.loads((cls.out_dir / "report.json").read_text(encoding="utf-8"))
        cls.records = [
            json.loads(line)
            for line in (cls.out_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cls.sha_to_out = {
            e["input_sha256"]: e["output"]
            for e in cls.report["files"]
            if e.get("status") == "sanitized" and e.get("output")
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _out_path(self, rel_input: str) -> Path:
        sha = hashlib.sha256((REPO_ROOT / "dev" / "input" / rel_input).read_bytes()).hexdigest()
        out_rel = self.sha_to_out[sha].replace("sanitized/", "", 1)
        return self.san_dir / out_rel

    def test_pseudonym_consistency_across_csv_json_fhir_and_text(self) -> None:
        rec_map = {(r["file"], r["loc"]): r["patient"] for r in self.records}

        # PT00015 across CSV, bulk JSON, lab report JSON, and renamed Discharge Summary TXT
        snj_path = self._out_path("registration/sanjeevani_registration.csv")
        snj_key = f"sanitized/{snj_path.relative_to(self.san_dir).as_posix()}"
        pid_pt15 = rec_map[(snj_key, "row:17")]
        assert pid_pt15 is not None
        snj_rows = list(csv.reader(io.StringIO(snj_path.read_text(encoding="utf-8"), newline="")))
        self.assertEqual(snj_rows[18][0], pid_pt15)

        bulk_path = self._out_path("lab/bulk_lab_export.json")
        bulk_key = f"sanitized/{bulk_path.relative_to(self.san_dir).as_posix()}"
        bulk_data = json.loads(bulk_path.read_text(encoding="utf-8"))
        self.assertEqual(rec_map[(bulk_key, "/results/45")], pid_pt15)
        self.assertEqual(bulk_data["results"][45]["uhid"], pid_pt15)

        lab_rep_path = self._out_path("lab/9368591915_report_0032.json")
        lab_rep_key = f"sanitized/{lab_rep_path.relative_to(self.san_dir).as_posix()}"
        lab_rep_data = json.loads(lab_rep_path.read_text(encoding="utf-8"))
        self.assertEqual(rec_map[(lab_rep_key, "doc")], pid_pt15)
        self.assertEqual(lab_rep_data["patient"]["hospital_uhid"], pid_pt15)

        ds_path = self._out_path("discharge/Discharge_Summary_Shahid_Mohammad_UHID-010607.txt")
        ds_key = f"sanitized/{ds_path.relative_to(self.san_dir).as_posix()}"
        self.assertEqual(rec_map[(ds_key, "doc")], pid_pt15)
        self.assertIn(pid_pt15, ds_path.read_text(encoding="utf-8"))

        # Clean protocol remains unlinked and intact
        clean_key = "sanitized/clean/dengue_management_protocol.txt"
        self.assertIsNone(rec_map[(clean_key, "doc")])
        self.assertIn("Paracetamol 650 mg", (self.san_dir / "clean/dengue_management_protocol.txt").read_text(encoding="utf-8"))

    def test_all_fhir_bundles_valid_with_resolved_patient_references(self) -> None:
        rec_map = {(r["file"], r["loc"]): r["patient"] for r in self.records}
        fhir_files = sorted((self.san_dir / "fhir").glob("*.json"))
        self.assertGreaterEqual(len(fhir_files), 15)

        for fpath in fhir_files:
            bundle = json.loads(fpath.read_text(encoding="utf-8"))
            self.assertEqual(bundle.get("resourceType"), "Bundle")
            entries = bundle.get("entry") or []
            patients = [e["resource"] for e in entries if e["resource"]["resourceType"] == "Patient"]
            self.assertEqual(len(patients), 1)
            pat_entry = next(e for e in entries if e["resource"]["resourceType"] == "Patient")
            pat_res = pat_entry["resource"]
            pid = pat_res["id"]

            fkey = f"sanitized/{fpath.relative_to(self.san_dir).as_posix()}"
            self.assertEqual(rec_map[(fkey, "doc")], pid)
            self.assertEqual(pat_entry.get("fullUrl"), f"Patient/{pid}")
            self.assertEqual(pat_res["identifier"][0]["value"], pid)

            # Collect all references inside entries and assert every clinical reference targets Patient/{pid}
            subject_refs: list[str] = []
            for e in entries:
                res = e["resource"]
                for k in ("subject", "patient"):
                    if isinstance(res.get(k), dict) and "reference" in res[k]:
                        subject_refs.append(res[k]["reference"])
            self.assertTrue(subject_refs)
            for ref in subject_refs:
                self.assertEqual(ref, f"Patient/{pid}")

    def test_cross_file_date_shift_and_length_of_stay_preservation(self) -> None:
        # 1. Same patient (PT00015) has identical shift across Sanjeevani DMY, Arogya MDY,
        # Billing claims, and Discharge Summary admission/discharge (5-day LOS).
        snj_rows = list(csv.reader(io.StringIO(self._out_path("registration/sanjeevani_registration.csv").read_text(encoding="utf-8"), newline="")))
        ar_rows = list(csv.reader(io.StringIO(self._out_path("registration/arogya_legacy_export.csv").read_text(encoding="utf-8"), newline="")))
        cl_rows = list(csv.reader(io.StringIO(self._out_path("billing/arogya_claims.csv").read_text(encoding="utf-8"), newline="")))

        snj_dob = dt.datetime.strptime(snj_rows[18][4], "%d/%m/%Y").date()
        ar_dob = dt.datetime.strptime(ar_rows[6][4], "%m/%d/%Y").date()
        self.assertEqual(snj_dob, ar_dob)
        self.assertNotEqual(snj_dob, dt.date(1986, 5, 30))

        shift_days = (snj_dob - dt.date(1986, 5, 30)).days
        self.assertNotEqual(shift_days, 0)

        # Billing claims: true admit 2026-01-13, discharge 2026-01-18 (5 days LOS)
        cl_admit = dt.datetime.strptime(cl_rows[1][8], "%d/%m/%Y").date()
        cl_disch = dt.datetime.strptime(cl_rows[1][9], "%d/%m/%Y").date()
        self.assertEqual((cl_admit - dt.date(2026, 1, 13)).days, shift_days)
        self.assertEqual((cl_disch - cl_admit).days, 5)

        # Discharge Summary TXT: true admit 01/13/2026, discharge 01/18/2026 (MDY)
        ds_txt = self._out_path("discharge/Discharge_Summary_Shahid_Mohammad_UHID-010607.txt").read_text(encoding="utf-8")
        exp_admit = (dt.date(2026, 1, 13) + dt.timedelta(days=shift_days)).strftime("%m/%d/%Y")
        exp_disch = (dt.date(2026, 1, 18) + dt.timedelta(days=shift_days)).strftime("%m/%d/%Y")
        self.assertIn(f"Date of Admission: {exp_admit}", ds_txt)
        self.assertIn(f"Date of Discharge: {exp_disch}", ds_txt)

        # 2. FHIR Bundle Encounter.period preserves exact 8-day LOS & time/tz suffix
        b0 = json.loads(self._out_path("fhir/bundle_08fb8ae4.json").read_text(encoding="utf-8"))
        enc = next(e["resource"] for e in b0["entry"] if e["resource"]["resourceType"] == "Encounter")
        start_s = enc["period"]["start"]
        end_s = enc["period"]["end"]
        self.assertTrue(start_s.endswith("T18:15:00+05:30"))
        self.assertTrue(end_s.endswith("T11:00:00+05:30"))
        self.assertEqual((dt.date.fromisoformat(end_s[:10]) - dt.date.fromisoformat(start_s[:10])).days, 8)

    def test_age_banding_and_clinical_preservation(self) -> None:
        self.assertGreater(self.report["counts"]["records_generalized"], 600)
        self.assertGreater(self.report["counts"]["records_suppressed"], 0)

        snj_rows = list(csv.reader(io.StringIO(self._out_path("registration/sanjeevani_registration.csv").read_text(encoding="utf-8"), newline="")))
        self.assertEqual(snj_rows[18][3], "30-39")
        self.assertEqual(snj_rows[18][9], "[SUPPRESSED]")
        self.assertEqual(snj_rows[18][10], "Sitapur")

        jjh_rows = list(csv.reader(io.StringIO(self._out_path("registration/jeevanjyoti_patients.tsv").read_text(encoding="utf-8"), newline=""), delimiter="\t"))
        self.assertEqual(jjh_rows[1][3], "40-49 वर्ष")
        self.assertEqual(jjh_rows[1][8], "[SUPPRESSED]")

        city_rows = list(csv.reader(io.StringIO(self._out_path("lab/city_results.csv").read_text(encoding="utf-8"), newline="")))
        self.assertEqual(city_rows[1][2], "60-69/F")
        self.assertEqual(city_rows[1][6], "13.5")

        lab_rep = json.loads(self._out_path("lab/9368591915_report_0032.json").read_text(encoding="utf-8"))
        self.assertEqual(lab_rep["patient"]["age"], "40-49")
        self.assertEqual(lab_rep["panels"][0]["results"][0]["value"], 13.6)

        wa_txt = self._out_path("lab/whatsapp_forward_0003.txt").read_text(encoding="utf-8")
        self.assertIn("45-49 Y / F", wa_txt)
        self.assertIn("Dolo 650 BD x 5 days", wa_txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
