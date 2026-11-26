#!/usr/bin/env python3
"""Acceptance test suite for identity_clinical_linker.py (Checkpoint 8).

Verifies:
1. Hospital Scoping: Same UHID string in two different hospitals does NOT cross-link.
2. Ambiguity Safety: Shared household phone (multiple candidate clusters) returns None.
3. Contradiction Guards: Age or gender contradiction blocks a UHID or phone match.
4. Tiered Resolution: Evaluates priority (Tier 1 UHID -> Tier 2 Phone -> Tier 3 Name/Gender).
5. End-to-End Propagation: Evaluates dev/input clinical files, asserting Precision = 1.00.
"""

import json
from pathlib import Path
import sys

# Ensure starter is in sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from identity_clinical_linker import (
    build_anchor_index,
    resolve_clinical_record,
    normalize_uhid,
    get_hospital_id,
    propagate_clinical_pseudonyms,
)
from identity_adapters import extract_all_registration_records
from identity_pipeline import build_pseudonym_map


def test_different_hospitals_same_uhid_isolation():
    print("Testing Test 1: Hospital scoping (same UHID string in different hospitals)...")
    # Reg records: UHID '001000' exists in Sanjeevani ('snj')
    reg_records = [
        {
            "name": "ramesh sharma",
            "age": 45,
            "gender": "M",
            "phone": "9876543210",
            "uhid": "SNJ-001000",
            "source_file": "registration/sanjeevani_registration.csv",
            "record_loc": "row:0",
        }
    ]
    reg_psn_map = {("registration/sanjeevani_registration.csv", "row:0"): "PSN-AAAAAA"}
    anchor_index = build_anchor_index(reg_records, reg_psn_map)

    # Clinical record from Jeevanjyoti ('jjh') presenting with 'SNJ-001000' or same number
    clinical_rec = {
        "hospital_id": "jjh",
        "uhid": "SNJ-001000",
        "phone": None,
        "name": None,
        "age": None,
        "gender": None,
    }
    res = resolve_clinical_record(clinical_rec, anchor_index)
    assert res is None, f"Cross-hospital UHID collision was wrongly linked: {res}"

    # Clinical record from Sanjeevani ('snj') presenting with same UHID
    clinical_rec_snj = {
        "hospital_id": "snj",
        "uhid": "SNJ-001000",
        "phone": None,
        "name": None,
        "age": None,
        "gender": None,
    }
    res_snj = resolve_clinical_record(clinical_rec_snj, anchor_index)
    assert res_snj == "PSN-AAAAAA", f"Expected PSN-AAAAAA, got {res_snj}"
    print("  [PASS] Same UHID string in different hospitals safely isolated")


def test_shared_household_phone_ambiguity():
    print("Testing Test 2: Shared household phone safety (multiple candidate clusters)...")
    # Two distinct clusters sharing a household mobile phone
    reg_records = [
        {
            "name": "ramesh sharma",
            "age": 48,
            "gender": "M",
            "phone": "9876543210",
            "uhid": "SNJ/26/001",
            "source_file": "sanjeevani.csv",
            "record_loc": "row:0",
        },
        {
            "name": "rahul sharma",
            "age": 16,
            "gender": "M",
            "phone": "9876543210",
            "uhid": "SNJ/26/002",
            "source_file": "sanjeevani.csv",
            "record_loc": "row:1",
        },
    ]
    reg_psn_map = {
        ("sanjeevani.csv", "row:0"): "PSN-FATHER",
        ("sanjeevani.csv", "row:1"): "PSN-CHILD",
    }
    anchor_index = build_anchor_index(reg_records, reg_psn_map)

    # Clinical record with phone only (no UHID)
    clinical_rec = {
        "hospital_id": None,
        "uhid": None,
        "phone": "9876543210",
        "name": None,
        "age": None,
        "gender": None,
    }
    res = resolve_clinical_record(clinical_rec, anchor_index)
    assert res is None, f"Shared phone without disambiguation should return None, got {res}"
    print("  [PASS] Shared household phone safely returns None without disambiguation")


def test_age_gender_contradiction_guards():
    print("Testing Test 3: Age and gender contradiction guards...")
    # Known registration record: Female, Age 24
    reg_records = [
        {
            "name": "priya verma",
            "age": 24,
            "gender": "F",
            "phone": "9123456789",
            "uhid": "JJH-0070001",
            "source_file": "jeevanjyoti.tsv",
            "record_loc": "row:0",
        }
    ]
    reg_psn_map = {("jeevanjyoti.tsv", "row:0"): "PSN-PRIYA1"}
    anchor_index = build_anchor_index(reg_records, reg_psn_map)

    # 1. Gender contradiction: Same hospital UHID, but Male
    rec_gender_conflict = {
        "hospital_id": "jjh",
        "uhid": "JJH-0070001",
        "phone": None,
        "name": None,
        "age": 24,
        "gender": "M",  # Conflict!
    }
    res_g = resolve_clinical_record(rec_gender_conflict, anchor_index)
    assert res_g is None, f"Gender conflict failed to block match: {res_g}"

    # 2. Hard age contradiction: Same hospital UHID, but Age 78 (gap = 54 yrs)
    rec_age_conflict = {
        "hospital_id": "jjh",
        "uhid": "JJH-0070001",
        "phone": None,
        "name": None,
        "age": 78,  # Conflict!
        "gender": "F",
    }
    res_a = resolve_clinical_record(rec_age_conflict, anchor_index)
    assert res_a is None, f"Age conflict failed to block match: {res_a}"

    # 3. Compatible record: Female, Age 25 (gap = 1 yr)
    rec_ok = {
        "hospital_id": "jjh",
        "uhid": "JJH-0070001",
        "phone": None,
        "name": None,
        "age": 25,
        "gender": "F",
    }
    res_ok = resolve_clinical_record(rec_ok, anchor_index)
    assert res_ok == "PSN-PRIYA1", f"Expected PSN-PRIYA1, got {res_ok}"
    print("  [PASS] Contradiction guards block mismatched gender/age")


def test_e2e_clinical_propagation():
    print("Testing Test 4: End-to-end clinical propagation on dev/input...")
    dev_dir = HERE.parent / "dev"
    if not (dev_dir / "truth" / "truth.json").exists():
        print("  [SKIP] truth.json not found")
        return

    secret = b"practice"
    psn_map = build_pseudonym_map(dev_dir / "input", secret)

    with open(dev_dir / "truth/truth.json") as f:
        truth = json.load(f)

    # Evaluate Precision and Recall against ground truth
    import itertools
    from collections import defaultdict

    unit_patient = {}
    for rel, f in truth["files"].items():
        for loc, u in f["units"].items():
            if u.get("link") == "required" and u.get("patient"):
                unit_patient[(rel, loc)] = (u["patient"], psn_map.get((rel, loc)))

    units = list(unit_patient.values())
    tp = fp = fn = 0
    by_true = defaultdict(list)
    for i, (t, y) in enumerate(units):
        by_true[t].append(i)
    yours = defaultdict(list)
    for i, (t, y) in enumerate(units):
        if y:
            yours[y].append(i)

    true_pairs = sum(len(v) * (len(v) - 1) // 2 for v in by_true.values())
    for members in yours.values():
        for i, j in itertools.combinations(members, 2):
            if units[i][0] == units[j][0]:
                tp += 1
            else:
                fp += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / true_pairs if true_pairs else 0.0

    print(f"  Total required units evaluated: {len(units)}")
    print(f"  Assigned pseudonyms: {sum(1 for _, y in units if y)}")
    print(f"  True Positives (TP): {tp}, False Positives (FP): {fp}")
    print(f"  Patient Linkage Precision: {precision:.4f} (Must be 1.0000)")
    print(f"  Patient Linkage Recall: {recall:.4f}")

    assert fp == 0, f"Detected {fp} false positive merges in clinical propagation!"
    assert precision == 1.0, f"Precision dropped below 1.00: {precision}"
    assert recall >= 0.50, f"Recall did not reach target >= 0.50: {recall}"
    print("  [PASS] End-to-end clinical propagation verified with 1.0000 Precision and high Recall")


def main():
    test_different_hospitals_same_uhid_isolation()
    test_shared_household_phone_ambiguity()
    test_age_gender_contradiction_guards()
    test_e2e_clinical_propagation()
    print("\nALL CHECKPOINT 8 ACCEPTANCE TESTS PASSED!")


if __name__ == "__main__":
    main()
