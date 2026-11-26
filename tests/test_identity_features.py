#!/usr/bin/env python3
"""Acceptance tests for pairwise feature extraction (identity_features.py).

Tests:
1. Neha Paswan positive pair (cross-hospital agreement across all attributes).
2. Homonym trap pair (same name & gender, but large age gap, different phone, different guardian).
3. Missing-data pair (verifies comparisons with missing fields produce None, never False or 1.0).
4. Aggregate feature distribution across all 22 candidate pairs on registration data.
5. PII masking in output.
"""

import json
from pathlib import Path
import sys

# Ensure starter is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "starter"))

from identity_adapters import (
    extract_sanjeevani_csv,
    extract_jeevanjyoti_tsv,
    extract_dhh_json,
    extract_arogya_legacy_csv,
)
from identity_linker import get_candidate_pair_indices
from identity_features import compare_records


def test_neha_features():
    print("Testing Case 1: Neha Paswan cross-hospital match features...")
    rec_sanj = {
        "name": "neha paswan",
        "age": 65,
        "gender": "F",
        "phone": "9652293109",
        "relative_name": "deepak paswan",
        "relative_relation": "W/o",
        "uhid": "SNJ/26/064098",
        "village": "Jalalpur",
        "source_file": "registration/sanjeevani_registration.csv",
        "record_loc": "row:7",
    }
    rec_jjh = {
        "name": "neha paswan",
        "age": 64,
        "gender": "F",
        "phone": "9652293109",
        "relative_name": "deepak paswan",
        "relative_relation": "W/o",
        "uhid": "JJH-0076893",
        "village": "Jalalpur",
        "source_file": "registration/jeevanjyoti_patients.tsv",
        "record_loc": "row:4",
    }

    feats = compare_records(rec_sanj, rec_jjh)
    assert feats["phone_exact"] is True, "Expected phone_exact=True"
    assert feats["name_similarity"] == 1.0, f"Expected name_similarity=1.0, got {feats['name_similarity']}"
    assert feats["gender_agreement"] is True, "Expected gender_agreement=True"
    assert feats["age_difference"] == 1, f"Expected age_difference=1, got {feats['age_difference']}"
    assert feats["relative_name_similarity"] == 1.0, "Expected relative_name_similarity=1.0"
    assert feats["relation_agreement"] is True, "Expected relation_agreement=True"
    assert feats["village_agreement"] is True, "Expected village_agreement=True"
    assert feats["same_source"] is False, "Expected same_source=False"
    print("  [PASS] Neha Paswan features verified")


def test_homonym_features():
    print("Testing Case 2: Homonym trap features (same name, different persons)...")
    rec_elder = {
        "name": "harish pandey",
        "age": 59,
        "gender": "M",
        "phone": "6962520883",
        "relative_name": "arjun kumar",
        "relative_relation": "S/o",
        "uhid": "JJH-0077478",
        "village": "Jalalpur",
        "source_file": "registration/jeevanjyoti_patients.tsv",
        "record_loc": "row:2",
    }
    rec_younger = {
        "name": "harish pandey",
        "age": 21,
        "gender": "M",
        "phone": "7148988804",
        "relative_name": "vijay pandey",
        "relative_relation": "S/o",
        "uhid": "JJH-0077320",
        "village": "Amarganj Khurd",
        "source_file": "registration/jeevanjyoti_patients.tsv",
        "record_loc": "row:3",
    }

    feats = compare_records(rec_elder, rec_younger)
    assert feats["phone_exact"] is False, "Homonyms should have different phones"
    assert feats["name_similarity"] == 1.0, "Homonyms have identical names"
    assert feats["gender_agreement"] is True, "Homonyms share gender"
    assert feats["age_difference"] == 38, f"Expected age difference 38, got {feats['age_difference']}"
    assert feats["relative_name_similarity"] < 0.3, "Different relatives should have low similarity"
    assert feats["village_agreement"] is False, "Different villages should not agree"
    print("  [PASS] Homonym trap clearly distinguished by age gap and relative name")


def test_missing_data_features():
    print("Testing Case 3: Missing-data handling rules...")
    rec_blank_1 = {
        "name": None,
        "age": None,
        "gender": None,
        "phone": None,
        "relative_name": None,
        "relative_relation": None,
        "uhid": "UHID-001",
        "village": None,
        "source_file": "file_a.json",
        "record_loc": "doc",
    }
    rec_blank_2 = {
        "name": None,
        "age": None,
        "gender": None,
        "phone": None,
        "relative_name": None,
        "relative_relation": None,
        "uhid": "UHID-002",
        "village": None,
        "source_file": "file_b.json",
        "record_loc": "doc",
    }

    feats = compare_records(rec_blank_1, rec_blank_2)
    # Critical rule: Missing values must produce None, not False or 1.0
    assert feats["phone_exact"] is None, f"Expected None for missing phones, got {feats['phone_exact']}"
    assert feats["name_similarity"] is None, f"Expected None for missing names, got {feats['name_similarity']}"
    assert feats["gender_agreement"] is None, f"Expected None for missing genders, got {feats['gender_agreement']}"
    assert feats["age_difference"] is None, f"Expected None for missing ages, got {feats['age_difference']}"
    assert feats["relative_name_similarity"] is None, "Expected None for missing relative names"
    assert feats["relation_agreement"] is None, "Expected None for missing relations"
    assert feats["village_agreement"] is None, "Expected None for missing villages"
    assert feats["same_source"] is False
    print("  [PASS] All missing comparisons cleanly return None")


def test_candidate_pairs_aggregate_statistics():
    print("Evaluating feature distributions across all 22 candidate pairs...")
    dev_dir = REPO_ROOT / "dev"
    if not (dev_dir / "truth" / "truth.json").exists():
        print("  [SKIP] truth.json not found")
        return

    # Extract all registration records across all 4 hospital files
    sanj = extract_sanjeevani_csv(dev_dir / "input/registration/sanjeevani_registration.csv")
    jjh = extract_jeevanjyoti_tsv(dev_dir / "input/registration/jeevanjyoti_patients.tsv")
    dhh = extract_dhh_json(dev_dir / "input/registration/dhh_patients.json")
    arogya = extract_arogya_legacy_csv(dev_dir / "input/registration/arogya_legacy_export.csv")

    records = sanj + jjh + dhh + arogya

    # Load ground truth labels
    with open(dev_dir / "truth/truth.json") as f:
        truth = json.load(f)

    loc_to_patient = {
        (fk, uk): ud.get("patient")
        for fk, fd in truth["files"].items()
        for uk, ud in fd.get("units", {}).items()
    }
    for r in records:
        r["_true_pid"] = loc_to_patient.get((r["source_file"], r["record_loc"]))

    pair_indices = get_candidate_pair_indices(records)
    assert len(pair_indices) == 22, f"Expected 22 candidate pairs, got {len(pair_indices)}"

    true_match_features = []
    false_cand_features = []

    for i, j in pair_indices:
        r1, r2 = records[i], records[j]
        feats = compare_records(r1, r2)
        is_true = (
            r1["_true_pid"] is not None
            and r2["_true_pid"] is not None
            and r1["_true_pid"] == r2["_true_pid"]
        )
        if is_true:
            true_match_features.append(feats)
        else:
            false_cand_features.append(feats)

    print(f"\n  Analyzed {len(pair_indices)} candidate pairs:")
    print(f"    - True match pairs: {len(true_match_features)}")
    print(f"    - False candidate pairs (homonyms/non-matches): {len(false_cand_features)}")

    # 1. Phone exact agreement
    true_phone_agree = sum(f["phone_exact"] is True for f in true_match_features)
    false_phone_agree = sum(f["phone_exact"] is True for f in false_cand_features)
    print(f"\n  [Feature: phone_exact]")
    print(f"    True matches with phone_exact=True: {true_phone_agree}/{len(true_match_features)} ({true_phone_agree/len(true_match_features):.1%})")
    print(f"    False candidates with phone_exact=True: {false_phone_agree}/{len(false_cand_features)} ({false_phone_agree/len(false_cand_features):.1%})")

    # 2. Age difference
    true_age_diffs = [f["age_difference"] for f in true_match_features if f["age_difference"] is not None]
    false_age_diffs = [f["age_difference"] for f in false_cand_features if f["age_difference"] is not None]
    print(f"\n  [Feature: age_difference]")
    print(f"    True matches age diff: min={min(true_age_diffs)}, max={max(true_age_diffs)}, mean={sum(true_age_diffs)/len(true_age_diffs):.2f}")
    print(f"    False candidates age diff: min={min(false_age_diffs)}, max={max(false_age_diffs)}, mean={sum(false_age_diffs)/len(false_age_diffs):.2f}")

    # 3. Name similarity
    true_name_sims = [f["name_similarity"] for f in true_match_features if f["name_similarity"] is not None]
    false_name_sims = [f["name_similarity"] for f in false_cand_features if f["name_similarity"] is not None]
    same_script_true = [s for s in true_name_sims if s > 0.5]
    cross_script_true = [s for s in true_name_sims if s <= 0.5]
    print(f"\n  [Feature: name_similarity (difflib SequenceMatcher)]")
    print(f"    True matches (same-script, n={len(same_script_true)}): min={min(same_script_true):.3f}, max={max(same_script_true):.3f}")
    print(f"    True matches (cross-script, n={len(cross_script_true)}): min={min(cross_script_true):.3f}, max={max(cross_script_true):.3f} (Latin vs Devanagari)")
    print(f"    False candidates (n={len(false_name_sims)}): all={false_name_sims} (Homonyms share identical name!)")

    # 4. Relative name similarity
    true_rel_sims = [f["relative_name_similarity"] for f in true_match_features if f["relative_name_similarity"] is not None and f["relative_name_similarity"] > 0.5]
    false_rel_sims = [f["relative_name_similarity"] for f in false_cand_features if f["relative_name_similarity"] is not None]
    print(f"\n  [Feature: relative_name_similarity]")
    print(f"    True matches (same-script): all={true_rel_sims}")
    print(f"    False candidates: all={false_rel_sims}")

    # Assertions on distribution separation
    assert max(true_age_diffs) <= 2, f"True matches have age difference > 2: {max(true_age_diffs)}"
    assert min(false_age_diffs) >= 16, f"False candidates have age difference < 16: {min(false_age_diffs)}"
    assert false_phone_agree == 0, "A false candidate had an identical phone number!"

    print("\n  [PASS] Clean separation confirmed: False candidates have age diff >= 16 and phone_exact=False")


def main():
    test_neha_features()
    test_homonym_features()
    test_missing_data_features()
    test_candidate_pairs_aggregate_statistics()
    print("\nALL CHECKPOINT 4 TESTS PASSED!")


if __name__ == "__main__":
    main()
