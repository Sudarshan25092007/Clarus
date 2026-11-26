#!/usr/bin/env python3
"""Acceptance tests for candidate generation and blocking (identity_linker.py).

Verifies:
1. Candidate generation proposed pairs match the blocking rules.
2. Neha Paswan's two records appear as a candidate pair.
3. Candidate pairs are strictly deduplicated (even if matching on multiple keys).
4. Records with missing phone can become candidates via name+gender or relative info.
5. Missing/null values never form shared blocks.
6. Original records are not mutated.
7. Evaluates Candidate Recall against ground truth on all registration files.
"""

import copy
import json
from pathlib import Path
import sys

# Ensure starter is in sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from identity_adapters import (
    extract_sanjeevani_csv,
    extract_jeevanjyoti_tsv,
    extract_dhh_json,
    extract_arogya_legacy_csv,
)
from identity_linker import (
    generate_candidate_pairs,
    get_candidate_pair_indices,
)


def test_synthetic_invariants():
    print("Testing candidate generation invariants (synthetic)...")

    # Record A & B: share phone, name, and relative (all 3 keys match)
    rec_a = {
        "name": "neha paswan",
        "age": 65,
        "gender": "F",
        "phone": "9652293109",
        "relative_name": "deepak paswan",
        "relative_relation": "W/o",
        "uhid": "SNJ/001",
        "village": "Jalalpur",
        "source_file": "file_a.csv",
        "record_loc": "row:0",
    }
    rec_b = {
        "name": "neha paswan",
        "age": 64,
        "gender": "F",
        "phone": "9652293109",
        "relative_name": "deepak paswan",
        "relative_relation": "W/o",
        "uhid": "JJH/002",
        "village": "Jalalpur",
        "source_file": "file_b.tsv",
        "record_loc": "row:0",
    }
    # Record C: missing phone, but shares name and gender with A & B
    rec_c = {
        "name": "neha paswan",
        "age": 65,
        "gender": "F",
        "phone": None,
        "relative_name": None,
        "relative_relation": None,
        "uhid": "DHH/003",
        "village": None,
        "source_file": "file_c.json",
        "record_loc": "row:0",
    }
    # Record D: all fields missing / null
    rec_d = {
        "name": None,
        "age": None,
        "gender": None,
        "phone": None,
        "relative_name": None,
        "relative_relation": None,
        "uhid": "UNK/004",
        "village": None,
        "source_file": "file_d.txt",
        "record_loc": "doc",
    }
    # Record E: all fields missing / null
    rec_e = {
        "name": None,
        "age": None,
        "gender": None,
        "phone": None,
        "relative_name": None,
        "relative_relation": None,
        "uhid": "UNK/005",
        "village": None,
        "source_file": "file_e.txt",
        "record_loc": "doc",
    }

    records = [rec_a, rec_b, rec_c, rec_d, rec_e]
    records_copy = copy.deepcopy(records)

    pairs = generate_candidate_pairs(records)

    # 1. Non-mutation invariant
    assert records == records_copy, "Candidate generation mutated input records!"

    # 2. Deduplication check: pair (rec_a, rec_b) must appear exactly once despite matching 3 keys
    ab_pairs = [
        p for p in pairs
        if (p[0]["uhid"] == "SNJ/001" and p[1]["uhid"] == "JJH/002")
        or (p[0]["uhid"] == "JJH/002" and p[1]["uhid"] == "SNJ/001")
    ]
    assert len(ab_pairs) == 1, f"Expected 1 pair for A-B, got {len(ab_pairs)}"

    # 3. Missing phone candidate check: rec_c (no phone) should be paired with rec_a and rec_b via name+gender
    ac_pairs = [
        p for p in pairs
        if (p[0]["uhid"] == "SNJ/001" and p[1]["uhid"] == "DHH/003")
        or (p[0]["uhid"] == "DHH/003" and p[1]["uhid"] == "SNJ/001")
    ]
    assert len(ac_pairs) == 1, "Missing phone record failed to pair via name+gender!"

    # 4. Null safety check: records D and E have missing fields and must NEVER pair with each other or anyone
    null_pairs = [
        p for p in pairs
        if p[0]["uhid"] in ("UNK/004", "UNK/005") or p[1]["uhid"] in ("UNK/004", "UNK/005")
    ]
    assert len(null_pairs) == 0, f"Null records formed candidate pairs: {null_pairs}"

    print("  [PASS] all synthetic blocking invariants verified")


def test_registration_recall():
    print("Testing candidate recall against ground truth on registration files...")
    dev_dir = HERE.parent / "dev"
    if not (dev_dir / "truth" / "truth.json").exists():
        print("  [SKIP] dev/truth/truth.json not found")
        return

    # Extract all registration records across all 4 hospital formats
    sanj = extract_sanjeevani_csv(dev_dir / "input/registration/sanjeevani_registration.csv")
    jjh = extract_jeevanjyoti_tsv(dev_dir / "input/registration/jeevanjyoti_patients.tsv")
    dhh = extract_dhh_json(dev_dir / "input/registration/dhh_patients.json")
    arogya = extract_arogya_legacy_csv(dev_dir / "input/registration/arogya_legacy_export.csv")

    records = sanj + jjh + dhh + arogya
    n_records = len(records)
    max_possible_pairs = n_records * (n_records - 1) // 2

    # Map records to ground truth patient IDs
    with open(dev_dir / "truth/truth.json") as f:
        truth = json.load(f)

    loc_to_patient = {
        (fk, uk): ud.get("patient")
        for fk, fd in truth["files"].items()
        for uk, ud in fd.get("units", {}).items()
    }

    for r in records:
        r["_true_pid"] = loc_to_patient.get((r["source_file"], r["record_loc"]))

    # Find all true positive matching pairs among registration records
    true_pairs = set()
    for i in range(n_records):
        for j in range(i + 1, n_records):
            p1 = records[i]["_true_pid"]
            p2 = records[j]["_true_pid"]
            if p1 is not None and p2 is not None and p1 == p2:
                true_pairs.add((i, j))

    # Generate candidate pairs using identity_linker
    pair_indices = set(get_candidate_pair_indices(records))

    captured = true_pairs.intersection(pair_indices)
    missed = true_pairs - pair_indices

    recall = len(captured) / len(true_pairs) if true_pairs else 1.0
    reduction = 1.0 - (len(pair_indices) / max_possible_pairs)

    print(f"  Total records: {n_records}")
    print(f"  Total possible pairs: {max_possible_pairs}")
    print(f"  Candidate pairs proposed: {len(pair_indices)} (search space reduced by {reduction:.1%})")
    print(f"  True matching pairs in data: {len(true_pairs)}")
    print(f"  True pairs captured: {len(captured)} / {len(true_pairs)}")
    print(f"  Candidate Recall: {recall:.1%}")

    # Assert 100% recall on registration files
    assert recall == 1.0, f"Candidate generation missed {len(missed)} true pairs: {missed}"

    # Verify Neha Paswan appears in candidates
    neha_indices = [idx for idx, r in enumerate(records) if r.get("name") == "neha paswan"]
    assert len(neha_indices) == 2, "Neha Paswan should appear in 2 registration files"
    neha_pair = (min(neha_indices), max(neha_indices))
    assert neha_pair in pair_indices, "Neha Paswan cross-hospital pair missing from candidates!"

    print("  [PASS] 100% Candidate Recall achieved on registration records!")


def main():
    test_synthetic_invariants()
    test_registration_recall()
    print("\nALL CANDIDATE GENERATION TESTS PASSED!")


if __name__ == "__main__":
    main()
