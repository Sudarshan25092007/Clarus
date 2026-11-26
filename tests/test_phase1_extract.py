"""Phase 1 unit tests and dev/input extraction coverage summary."""
from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter.identity import IdentityResolver, RecordIdentity, extract_identity  # noqa: E402


def _decode(raw: bytes) -> str | None:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="strict")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _iter_dev_records(input_dir: Path):
    """Yield (subfolder, relpath, loc, raw_fields, raw_text) or failure tuples."""
    for path in sorted(p for p in input_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(input_dir).as_posix()
        sub = rel.split("/", 1)[0]
        ext = path.suffix.lower()

        if ext not in (".csv", ".tsv", ".json", ".txt"):
            yield ("FAIL", sub, rel, f"unsupported_format:{ext}")
            continue
        raw = path.read_bytes()
        if not raw.strip():
            yield ("FAIL", sub, rel, "empty_file")
            continue
        text = _decode(raw)
        if text is None:
            yield ("FAIL", sub, rel, "undecodable_text")
            continue

        if ext in (".csv", ".tsv"):
            delim = "\t" if ext == ".tsv" else ","
            try:
                rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
            except csv.Error as exc:
                yield ("FAIL", sub, rel, f"csv_error:{exc}")
                continue
            if not rows:
                yield ("FAIL", sub, rel, "empty_csv")
                continue
            header, body = rows[0], rows[1:]
            if not body:
                yield ("FAIL", sub, rel, "header_only_no_rows")
                continue
            for i, row in enumerate(body):
                row_dict = {header[j]: row[j] if j < len(row) else "" for j in range(len(header))}
                yield ("OK", sub, rel, f"row:{i}", row_dict, "")

        elif ext == ".json":
            try:
                old_limit = sys.getrecursionlimit()
                sys.setrecursionlimit(5000)
                try:
                    data = json.loads(text)
                finally:
                    sys.setrecursionlimit(old_limit)
            except (ValueError, RecursionError) as exc:
                yield ("FAIL", sub, rel, f"json_error:{type(exc).__name__}")
                continue

            if not isinstance(data, dict):
                yield ("FAIL", sub, rel, f"unexpected_json_root:{type(data).__name__}")
                continue

            # Record-list JSONs (OPD visits, bulk lab results, DHH registration patients)
            list_key = None
            for candidate in ("visits", "results", "patients"):
                val = data.get(candidate)
                if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
                    list_key = candidate
                    break

            if data.get("resourceType") == "Bundle":
                yield ("OK", sub, rel, "doc", data, "")
            elif list_key:
                meta = {}
                if isinstance(data.get("_export_meta"), dict):
                    meta["_export_meta"] = data["_export_meta"]
                if isinstance(data.get("facility"), dict):
                    meta["facility"] = data["facility"]
                for i, item in enumerate(data[list_key]):
                    yield ("OK", sub, rel, f"/{list_key}/{i}", {**meta, **item}, "")
            else:
                yield ("OK", sub, rel, "doc", data, "")

        else:  # .txt
            yield ("OK", sub, rel, "doc", {}, text)


class Phase1ExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dev_input = REPO_ROOT / "dev" / "input"

    def test_csv_registration_hindi_and_legacy(self) -> None:
        # Sanjeevani CSV (UTF-8-SIG)
        snj = _decode((self.dev_input / "registration/sanjeevani_registration.csv").read_bytes())
        rows = list(csv.reader(io.StringIO(snj, newline="")))
        row0 = dict(zip(rows[0], rows[1]))
        rec0 = extract_identity("registration/sanjeevani_registration.csv", "row:0", row0)
        self.assertIsInstance(rec0, RecordIdentity)
        self.assertEqual(rec0.hospital_hint, "sanjeevani")
        self.assertIn("Mahesh Chauhan", rec0.names)
        self.assertIn(("S/o", "Naresh Chauhan"), rec0.relatives)
        self.assertEqual(rec0.mrns, ["SNJ/26/064127"])
        self.assertEqual(rec0.aadhaar, ["397205839628"])
        self.assertEqual(rec0.abha, ["91-7938-3729-8403"])
        self.assertEqual(rec0.dob, "12/04/1977")
        self.assertEqual(rec0.age, 48)
        self.assertEqual(rec0.gender, "M")
        self.assertEqual(rec0.village, "Durgamau Kalan")
        self.assertEqual(rec0.pin, "229073")

        # Jeevan Jyoti TSV (UTF-16, Hindi columns & Devanagari text)
        jjh = _decode((self.dev_input / "registration/jeevanjyoti_patients.tsv").read_bytes())
        trows = list(csv.reader(io.StringIO(jjh, newline=""), delimiter="\t"))
        trow0 = dict(zip(trows[0], trows[1]))
        trec0 = extract_identity("registration/jeevanjyoti_patients.tsv", "row:0", trow0)
        self.assertEqual(trec0.hospital_hint, "jeevanjyoti")
        self.assertIn("कमला निषाद", trec0.names)
        self.assertIn(("D/o", "किशन निषाद"), trec0.relatives)
        self.assertEqual(trec0.age, 47)
        self.assertEqual(trec0.gender, "F")
        self.assertEqual(trec0.pin, "225127")

    def test_json_dhh_and_fhir_bundle(self) -> None:
        dhh = json.loads(_decode((self.dev_input / "registration/dhh_patients.json").read_bytes()))
        p0 = {**{"facility": dhh["facility"]}, **dhh["patients"][0]}
        rec = extract_identity("registration/dhh_patients.json", "/patients/0", p0)
        self.assertEqual(rec.hospital_hint, "dhhardoi")
        self.assertEqual(rec.names, ["Sunita Patel", "सुनीता पटेल"])
        self.assertIn(("D/o", "Ramesh Patel"), rec.relatives)
        self.assertEqual(rec.aadhaar, ["902115225016"])
        self.assertEqual(rec.gender, "F")
        self.assertEqual(rec.pin, "241606")

        bundle = json.loads(_decode((self.dev_input / "fhir/bundle_08fb8ae4.json").read_bytes()))
        frec = extract_identity("fhir/bundle_08fb8ae4.json", "doc", bundle)
        self.assertEqual(frec.hospital_hint, "sanjeevani")
        self.assertIn("Shabana Bano", frec.names)
        self.assertIn("SNJ/26/064545", frec.mrns)
        self.assertIn("IPD-2026-00474", frec.mrns)
        self.assertIn("91-3444-7750-0940", frec.abha)
        self.assertIn("+91-6838656785", frec.phones)
        self.assertIn(("Husband", "Shahid Siddiqui"), frec.relatives)
        self.assertEqual(frec.dob, "1998-10-19")
        self.assertEqual(frec.gender, "F")
        self.assertEqual(frec.pin, "226183")

    def test_text_hinglish_hindi_and_clean_traps(self) -> None:
        note = _decode((self.dev_input / "notes/nursing_adm000161.txt").read_bytes())
        nrec = extract_identity("notes/nursing_adm000161.txt", "doc", {}, note)
        self.assertIn("ADM000161", nrec.mrns)
        self.assertIn("Shahid sahab", nrec.names)
        self.assertIn("Mohd. Shahid", nrec.names)
        self.assertIn(("S/o", "Zubair"), nrec.relatives)

        rx = _decode((self.dev_input / "prescriptions/rx_arogya_00030.txt").read_bytes())
        rrec = extract_identity("prescriptions/rx_arogya_00030.txt", "doc", {}, rx)
        self.assertIn("मोहम्मद शाहिद", rrec.names)
        self.assertEqual(rrec.age, 39)
        self.assertEqual(rrec.village, "मोहनगंज कलां")

        ds_hi = _decode((self.dev_input / "discharge/arogya_ds_ADM000170.txt").read_bytes())
        drec = extract_identity("discharge/arogya_ds_ADM000170.txt", "doc", {}, ds_hi)
        self.assertIn("मनोज यादव", drec.names)
        self.assertIn(("Father", "रमेश यादव"), drec.relatives)
        self.assertIn("8921678541", drec.phones)
        self.assertEqual(drec.pin, "261699")

        proto = _decode((self.dev_input / "clean/dengue_management_protocol.txt").read_bytes())
        prec = extract_identity("clean/dengue_management_protocol.txt", "doc", {}, proto)
        self.assertEqual(prec.names, [])
        self.assertEqual(prec.relatives, [])
        self.assertEqual(prec.phones, [])

        wa = _decode((self.dev_input / "lab/whatsapp_forward_0003.txt").read_bytes())
        wrec = extract_identity("lab/whatsapp_forward_0003.txt", "doc", {}, wa)
        self.assertIn("Asha ji", wrec.names)
        self.assertEqual(wrec.age, 48)
        self.assertEqual(wrec.gender, "F")
        self.assertIn("8069773173", wrec.phones)
        self.assertIn(("Relative", "Kamal Bhaiya"), wrec.relatives)

        opd_babu = extract_identity(
            "opd/arogya_opd_2026_03.json",
            "/visits/0",
            {"patient_ref": "UHID-010607", "notes": "Shahid babu ka BP 105/77, raat ko neend theek se aayi."},
        )
        self.assertIn("Shahid babu", opd_babu.names)

    def test_resolver_skeleton_stub(self) -> None:
        resolver = IdentityResolver()
        resolver.add_record("notes/nursing_adm000161.txt", "doc", {}, "Mohd. Shahid - K/C/O addison's disease")
        resolver.resolve_all()
        self.assertEqual(len(resolver.records), 1)
        self.assertIsNone(resolver.get_patient_id("notes/nursing_adm000161.txt", "doc"))
        self.assertIsNone(resolver.get_patient_key("notes/nursing_adm000161.txt", "doc"))


def run_coverage_report() -> None:
    dev_input = REPO_ROOT / "dev" / "input"
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    failures: list[tuple[str, str, str]] = []

    for item in _iter_dev_records(dev_input):
        if item[0] == "FAIL":
            _, sub, rel, reason = item
            failures.append((sub, rel, reason))
            stats[sub]["failed_files"] += 1
            continue

        _, sub, rel, loc, raw_fields, raw_text = item
        rec = extract_identity(rel, loc, raw_fields, raw_text)
        s = stats[sub]
        s["records"] += 1
        s["names"] += int(bool(rec.names))
        s["phones"] += int(bool(rec.phones))
        s["mrns"] += int(bool(rec.mrns))
        s["abha"] += int(bool(rec.abha))
        s["aadhaar"] += int(bool(rec.aadhaar))
        s["abha_or_aadhaar"] += int(bool(rec.abha or rec.aadhaar))
        s["pan_or_ins"] += int(bool(rec.pan or rec.insurance_ids))
        s["dob_or_age"] += int(rec.dob is not None or rec.age is not None)
        s["gender"] += int(rec.gender is not None)
        s["relatives"] += int(bool(rec.relatives))
        s["village_or_pin"] += int(rec.village is not None or rec.pin is not None)

    totals: dict[str, int] = defaultdict(int)
    print("\n=== Phase 1 Extraction Coverage Summary (dev/input/) ===")
    hdr = (
        f"{'Subfolder':<14} {'Recs':>5} {'Name':>5} {'Phone':>5} {'MRN':>5} "
        f"{'ABHA/Aad':>8} {'DOB/Age':>7} {'Rel':>5} {'Fail':>5}"
    )
    print(hdr)
    print("-" * len(hdr))
    for sub in sorted(stats):
        s = stats[sub]
        for k, v in s.items():
            totals[k] += v
        print(
            f"{sub:<14} {s['records']:>5} {s['names']:>5} {s['phones']:>5} {s['mrns']:>5} "
            f"{s['abha_or_aadhaar']:>8} {s['dob_or_age']:>7} {s['relatives']:>5} {s['failed_files']:>5}"
        )
    print("-" * len(hdr))
    print(
        f"{'TOTAL':<14} {totals['records']:>5} {totals['names']:>5} {totals['phones']:>5} "
        f"{totals['mrns']:>5} {totals['abha_or_aadhaar']:>8} {totals['dob_or_age']:>7} "
        f"{totals['relatives']:>5} {totals['failed_files']:>5}"
    )
    if failures:
        print("\nFiles skipped / failed parsing:")
        for sub, rel, reason in failures:
            print(f"  - [{sub}] {rel}: {reason}")


if __name__ == "__main__":
    run_coverage_report()
    unittest.main(verbosity=2)
