#!/usr/bin/env python3
"""Comprehensive test suite for identity extraction and adapters.

Tests:
1. Normalization functions (name, age, gender, phone, relative name & relation)
2. Edge cases (dummy phone numbers, international prefixes, leading 0 STD prefix)
3. Relative name relation prefix stripping
4. Registration format adapters:
   - Sanjeevani CSV (UTF-8)
   - Jeevanjyoti TSV (UTF-16)
   - District Hospital Hardoi JSON (nested JSON)
5. Cross-hospital patient linkage signal validation (Neha Paswan)
6. Zero-leak output compliance (masked logging)
"""

import json
from pathlib import Path
import sys

# Ensure starter is in sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from identity_extractor import (
    normalize_name,
    normalize_age,
    normalize_gender,
    normalize_phone,
    normalize_relative_name,
    normalize_relation,
    split_relation_and_name,
    normalize_identity,
)
from identity_adapters import (
    extract_sanjeevani_csv,
    extract_jeevanjyoti_tsv,
    extract_dhh_json,
)

CANONICAL_KEYS = {
    "name",
    "age",
    "gender",
    "phone",
    "relative_name",
    "relative_relation",
    "uhid",
    "village",
    "source_file",
    "record_loc",
}


def mask_str(s: str | None, keep_edges: int = 2) -> str | None:
    """Mask string for safe logging/printing without leaking PII."""
    if s is None:
        return None
    if len(s) <= keep_edges * 2:
        return s[:1] + "***" + s[-1:] if len(s) > 1 else "***"
    return s[:keep_edges] + "***" + s[-keep_edges:]


def mask_record_for_print(rec: dict) -> dict:
    """Create a masked copy of normalized record safe for terminal display."""
    return {
        "name": mask_str(rec.get("name")),
        "age": rec.get("age"),
        "gender": rec.get("gender"),
        "phone": mask_str(rec.get("phone")),
        "relative_name": mask_str(rec.get("relative_name")),
        "relative_relation": rec.get("relative_relation"),
        "uhid": mask_str(rec.get("uhid"), keep_edges=4),
        "source_file": rec.get("source_file"),
        "record_loc": rec.get("record_loc"),
    }


def test_normalize_name():
    print("Testing normalize_name...")
    assert normalize_name("Mohammad Shahid") == "mohammad shahid"
    assert normalize_name("  VIJAY  YADAV  ") == "vijay yadav"
    assert normalize_name('"Anita Sharma"') == "anita sharma"
    assert normalize_name("'Sunil Lodhi'") == "sunil lodhi"
    assert normalize_name("Mohd. Arif") == "mohd. arif"
    assert normalize_name("Mary-Jane") == "mary-jane"
    assert normalize_name("  ,Mahesh Chauhan;  ") == "mahesh chauhan"
    assert normalize_name("कमला निषाद") == "कमला निषाद"
    assert normalize_name("  मोहम्मद   शाहिद  ") == "मोहम्मद शाहिद"
    assert normalize_name("सुनीता पटेल") == "सुनीता पटेल"
    assert normalize_name("") is None
    assert normalize_name("   ") is None
    assert normalize_name(None) is None
    print("  [PASS] normalize_name")


def test_normalize_age():
    print("Testing normalize_age...")
    assert normalize_age(42) == 42
    assert normalize_age(0) == 0  # Infant
    assert normalize_age("0") == 0
    assert normalize_age("47") == 47
    assert normalize_age(48.0) == 48
    assert normalize_age("47 years") == 47
    assert normalize_age("47 yrs") == 47
    assert normalize_age("52 Yrs/F") == 52
    assert normalize_age("48/F") == 48
    assert normalize_age("95/M") == 95
    assert normalize_age("47 वर्ष") == 47
    assert normalize_age("21 वर्ष") == 21
    assert normalize_age("४७ वर्ष") == 47
    assert normalize_age(None) is None
    assert normalize_age("") is None
    assert normalize_age("unknown") is None
    assert normalize_age(200) is None
    assert normalize_age(-5) is None
    print("  [PASS] normalize_age")


def test_normalize_gender():
    print("Testing normalize_gender...")
    assert normalize_gender("M") == "M"
    assert normalize_gender("male") == "M"
    assert normalize_gender("F") == "F"
    assert normalize_gender("female") == "F"
    assert normalize_gender("48/F") == "F"
    assert normalize_gender("95/M") == "M"
    assert normalize_gender("पुरुष") == "M"
    assert normalize_gender("पु") == "M"
    assert normalize_gender("महिला") == "F"
    assert normalize_gender("स्त्री") == "F"
    assert normalize_gender(None) is None
    assert normalize_gender("") is None
    assert normalize_gender("other") is None
    print("  [PASS] normalize_gender")


def test_normalize_phone():
    print("Testing normalize_phone...")
    # Standard formats
    assert normalize_phone(9876543210) == "9876543210"
    assert normalize_phone("+91 98765-43210") == "9876543210"
    assert normalize_phone("91-9876543210") == "9876543210"

    # Leading zero policy (dataset-backed STD trunk dialling rule)
    assert normalize_phone("09876543210") == "9876543210"
    assert normalize_phone("060062-72163") == "6006272163"

    # Extended prefix patterns requested in Checkpoint 2 corrections
    assert normalize_phone("+91 (0) 96522-93109") == "9652293109"
    assert normalize_phone("0091 9652293109") == "9652293109"

    # Plausibility checks: dummy numbers and non-phone strings
    assert normalize_phone("9999999999") is None  # Repeated dummy
    assert normalize_phone("0000000000") is None  # Repeated dummy / non-mobile
    assert normalize_phone("1234567890") is None  # Starts with 1 (not 6..9)
    assert normalize_phone("12345") is None
    assert normalize_phone("UHID-9876543210") is None
    assert normalize_phone(None) is None
    assert normalize_phone("397205839628") is None  # Aadhaar

    # Devanagari numerals
    assert normalize_phone("७५६००१००३४") == "7560010034"
    assert normalize_phone("०९६५२२-९३१०९") == "9652293109"
    print("  [PASS] normalize_phone")


def test_normalize_relative_name():
    print("Testing normalize_relative_name and relation splitting...")
    # Direct split testing
    rel, name = split_relation_and_name(" S/o   Zubair ")
    assert rel == "S/o"
    assert name == "zubair"

    rel, name = split_relation_and_name("W/O: Ramesh Kumar")
    assert rel == "W/o"
    assert name == "ramesh kumar"

    rel, name = split_relation_and_name("पिता: रमेश कुमार")
    assert rel == "F/o"
    assert name == "रमेश कुमार"

    rel, name = split_relation_and_name("D/o किशन निषाद")
    assert rel == "D/o"
    assert name == "किशन निषाद"

    # normalize_relative_name default strips prefixes
    assert normalize_relative_name(" S/o   Zubair ") == "zubair"
    assert normalize_relative_name("W/O: Ramesh Kumar") == "ramesh kumar"
    assert normalize_relative_name("पिता: रमेश कुमार") == "रमेश कुमार"
    assert normalize_relative_name("Naresh Chauhan") == "naresh chauhan"
    assert normalize_relative_name("") is None
    assert normalize_relative_name(None) is None
    print("  [PASS] normalize_relative_name")


def test_normalize_identity_combined():
    print("Testing normalize_identity...")
    raw = {
        "name": "  Mohammad Shahid  ",
        "age": "39",
        "gender": "M",
        "phone": "+91 93685 91915",
        "uhid": "SNJ/26/064258",
        "relative_name": "S/o Zubair",
        "source_file": "registration/sanjeevani_registration.csv",
        "record_loc": "row:17",
    }
    norm = normalize_identity(raw)
    assert norm["name"] == "mohammad shahid"
    assert norm["age"] == 39
    assert norm["gender"] == "M"
    assert norm["phone"] == "9368591915"
    assert norm["relative_name"] == "zubair"  # Clean relative name (prefix stripped)
    assert norm["relative_relation"] == "S/o"  # Canonical relation
    assert norm["uhid"] == "SNJ/26/064258"
    assert norm["source_file"] == "registration/sanjeevani_registration.csv"
    assert norm["record_loc"] == "row:17"
    print("  [PASS] normalize_identity")


def test_registration_adapters():
    print("Testing registration format adapters on dev/input/...")
    dev_dir = HERE.parent / "dev" / "input"
    if not dev_dir.exists():
        print("  [SKIP] dev/input not found at", dev_dir)
        return

    # 1. Sanjeevani CSV Adapter
    sanj_file = dev_dir / "registration" / "sanjeevani_registration.csv"
    assert sanj_file.exists(), "sanjeevani_registration.csv missing"
    sanj_records = extract_sanjeevani_csv(sanj_file)
    assert len(sanj_records) == 27, f"Expected 27 Sanjeevani records, got {len(sanj_records)}"
    for idx, r in enumerate(sanj_records):
        assert set(r.keys()) == CANONICAL_KEYS
        assert r["record_loc"] == f"row:{idx}"
        assert r["source_file"].endswith("sanjeevani_registration.csv")

    # 2. Jeevanjyoti TSV Adapter
    jjh_file = dev_dir / "registration" / "jeevanjyoti_patients.tsv"
    assert jjh_file.exists(), "jeevanjyoti_patients.tsv missing"
    jjh_records = extract_jeevanjyoti_tsv(jjh_file)
    assert len(jjh_records) == 30, f"Expected 30 Jeevanjyoti records, got {len(jjh_records)}"
    for idx, r in enumerate(jjh_records):
        assert set(r.keys()) == CANONICAL_KEYS
        assert r["record_loc"] == f"row:{idx}"
        assert r["source_file"].endswith("jeevanjyoti_patients.tsv")

    # 3. DHH JSON Adapter
    dhh_file = dev_dir / "registration" / "dhh_patients.json"
    assert dhh_file.exists(), "dhh_patients.json missing"
    dhh_records = extract_dhh_json(dhh_file)
    assert len(dhh_records) == 1, f"Expected 1 DHH record, got {len(dhh_records)}"
    for idx, r in enumerate(dhh_records):
        assert set(r.keys()) == CANONICAL_KEYS
        assert r["record_loc"] == f"/patients/{idx}"
        assert r["source_file"].endswith("dhh_patients.json")

    # 4. Cross-hospital patient linkage signal validation (Neha Paswan)
    neha_sanj = [r for r in sanj_records if r["name"] == "neha paswan"]
    neha_jjh = [r for r in jjh_records if r["name"] == "neha paswan"]
    assert len(neha_sanj) == 1, "Neha Paswan not found in Sanjeevani records"
    assert len(neha_jjh) == 1, "Neha Paswan not found in Jeevanjyoti records"

    s_rec = neha_sanj[0]
    j_rec = neha_jjh[0]

    assert s_rec["name"] == j_rec["name"] == "neha paswan"
    assert s_rec["gender"] == j_rec["gender"] == "F"
    assert s_rec["phone"] == j_rec["phone"] == "9652293109"
    assert s_rec["relative_name"] == j_rec["relative_name"] == "deepak paswan"
    assert s_rec["relative_relation"] == j_rec["relative_relation"] == "W/o"

    # Hospital-specific UHIDs remain distinct
    assert s_rec["uhid"] == "SNJ/26/064098"
    assert j_rec["uhid"] == "JJH-0076893"

    print("  [PASS] all 3 registration adapters passed successfully")
    print("  [PASS] Neha Paswan cross-hospital match validated across Sanjeevani & Jeevanjyoti")

    # Display masked sample records
    print("\nSample Extracted Records (Masked):")
    sample_picks = [sanj_records[0], jjh_records[0], dhh_records[0]]
    for rec in sample_picks:
        masked = mask_record_for_print(rec)
        print(" ", json.dumps(masked, ensure_ascii=False))


def main():
    test_normalize_name()
    test_normalize_age()
    test_normalize_gender()
    test_normalize_phone()
    test_normalize_relative_name()
    test_normalize_identity_combined()
    test_registration_adapters()
    print("\nALL CHECKPOINT 3 TESTS PASSED!")


if __name__ == "__main__":
    main()
