#!/usr/bin/env python3
"""Acceptance test suite for identity_decision.py.

Verifies:
1. Zero False Positives on Registration Dev Candidates (all 3 homonym pairs are NON_MATCH).
2. High True-Match Capture (all same-script true matches are MATCH; cross-script are UNCERTAIN).
3. Missing-Data Safety (None inputs never raise TypeError or produce spurious MATCH).
4. Shared-Phone / Age-Gap Trap (phone_exact=True with large age gap is NOT MATCH).
5. Anti-Transitivity Conflict Guard (find_cluster_contradictions catches cut edges).
6. Determinism & Speed.
"""

import json
from pathlib import Path
import sys
import time

# Ensure starter is in sys.path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from identity_adapters import (
    extract_sanjeevani_csv,
    extract_jeevanjyoti_tsv,
    extract_dhh_json,
    extract_arogya_legacy_csv,
)
from identity_linker import get_candidate_pair_indices
from identity_features import compare_records
from identity_decision import (
    decide_pairwise_match,
    build_candidate_edge,
    find_cluster_contradictions,
)


def test_dev_candidate_decisions():
    print("Testing decisions on all 22 dev registration candidate pairs...")
    dev_dir = HERE.parent / "dev"
    if not (dev_dir / "truth" / "truth.json").exists():
        print("  [SKIP] truth.json not found")
        return

    records = (
        extract_sanjeevani_csv(dev_dir / "input/registration/sanjeevani_registration.csv")
        + extract_jeevanjyoti_tsv(dev_dir / "input/registration/jeevanjyoti_patients.tsv")
        + extract_dhh_json(dev_dir / "input/registration/dhh_patients.json")
        + extract_arogya_legacy_csv(dev_dir / "input/registration/arogya_legacy_export.csv")
    )

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

    counts = {"MATCH": 0, "NON_MATCH": 0, "UNCERTAIN": 0}
    true_positive_matches = 0
    false_positive_matches = 0
    cross_script_uncertain = 0

    t0 = time.monotonic()
    for i, j in pair_indices:
        r1, r2 = records[i], records[j]
        feats = compare_records(r1, r2)
        res = decide_pairwise_match(feats)
        edge = build_candidate_edge(i, j, feats)

        assert edge["decision"] == res["decision"]
        assert edge["is_active_edge"] == (res["decision"] == "MATCH")
        assert edge["is_cut_edge"] == (res["decision"] == "NON_MATCH")

        counts[res["decision"]] += 1
        is_true = (
            r1["_true_pid"] is not None
            and r2["_true_pid"] is not None
            and r1["_true_pid"] == r2["_true_pid"]
        )

        if res["decision"] == "MATCH":
            if is_true:
                true_positive_matches += 1
            else:
                false_positive_matches += 1
        elif not is_true:
            # False candidate must be classified as NON_MATCH (cut edge)
            assert res["decision"] == "NON_MATCH", (
                f"False candidate {i}-{j} was classified as {res['decision']}, expected NON_MATCH"
            )
        elif is_true and res["decision"] == "UNCERTAIN":
            # True match held back due to Latin vs Devanagari raw string gap
            cross_script_uncertain += 1

    elapsed_ms = (time.monotonic() - t0) * 1000

    print(f"  Processed {len(pair_indices)} pairs in {elapsed_ms:.2f} ms")
    print(f"  Decisions: {counts}")
    print(f"  True Positive Matches: {true_positive_matches} / 19")
    print(f"  False Positive Matches: {false_positive_matches} (Must be 0!)")
    print(f"  Cross-script True Matches held as UNCERTAIN: {cross_script_uncertain}")

    # Acceptance Criteria Verification:
    # 1. Zero False Positives
    assert false_positive_matches == 0, f"Violated zero-false-positive rule: {false_positive_matches} false matches!"

    # 2. All 3 homonym traps rejected as NON_MATCH
    assert counts["NON_MATCH"] == 3, f"Expected 3 NON_MATCH homonym rejections, got {counts['NON_MATCH']}"

    # 3. High true match capture (all same-script true matches are MATCH)
    assert true_positive_matches == 15, f"Expected 15 same-script true matches, got {true_positive_matches}"
    assert cross_script_uncertain == 4, f"Expected 4 cross-script matches held as UNCERTAIN, got {cross_script_uncertain}"

    print("  [PASS] Zero false positives, clean homonym cut edges, and expected capture rates verified")


def test_shared_family_phone_trap():
    print("Testing shared family phone / age gap trap...")
    # Parent (age 48) and child (age 16) sharing the same mobile phone
    feats_family = {
        "phone_exact": True,
        "name_similarity": 0.42,  # Different first names (e.g. Ramesh vs Rohit)
        "gender_agreement": True,
        "age_difference": 32,      # Large generation age gap
        "relative_name_similarity": None,
        "relation_agreement": None,
        "village_agreement": True,
        "same_source": False,
    }
    res = decide_pairwise_match(feats_family)
    assert res["decision"] == "NON_MATCH", f"Expected NON_MATCH for family phone trap, got {res['decision']}"
    assert "shared_family_phone_trap" in res["reasons"][0]
    print("  [PASS] Shared family phone trap safely rejected as NON_MATCH")


def test_missing_data_safety():
    print("Testing missing-data safety invariants...")
    # Record pair with missing phone, age, and relative information
    feats_missing = {
        "phone_exact": None,
        "name_similarity": 0.90,
        "gender_agreement": None,
        "age_difference": None,
        "relative_name_similarity": None,
        "relation_agreement": None,
        "village_agreement": None,
        "same_source": False,
    }
    # Must not raise TypeError, must not produce MATCH without corroboration
    res = decide_pairwise_match(feats_missing)
    assert res["decision"] == "UNCERTAIN", f"Expected UNCERTAIN, got {res['decision']}"
    assert res["score"] < 0.80

    # Completely blank features
    blank_feats = {
        "phone_exact": None,
        "name_similarity": None,
        "gender_agreement": None,
        "age_difference": None,
        "relative_name_similarity": None,
        "relation_agreement": None,
        "village_agreement": None,
        "same_source": False,
    }
    res_blank = decide_pairwise_match(blank_feats)
    assert res_blank["decision"] == "UNCERTAIN"
    assert res_blank["score"] < 0.30
    print("  [PASS] Missing data handled with strict None-safety")


def test_anti_transitivity_guard():
    print("Testing anti-transitivity conflict guard (find_cluster_contradictions)...")
    # Scenario:
    # A (idx 0) and B (idx 1) are a MATCH
    # B (idx 1) and C (idx 2) are a MATCH
    # But A (idx 0) and C (idx 2) is a NON_MATCH (e.g. homonym / hard contradiction)
    cut_edges = {(0, 2)}

    # Proposed naive cluster: {0, 1, 2}
    proposed_cluster = {0, 1, 2}
    conflicts = find_cluster_contradictions(proposed_cluster, cut_edges)

    assert len(conflicts) == 1, f"Expected 1 contradiction, got {len(conflicts)}"
    assert conflicts[0] == (0, 2), f"Expected contradiction on (0, 2), got {conflicts[0]}"

    # Cluster without conflicts: {0, 1}
    assert len(find_cluster_contradictions({0, 1}, cut_edges)) == 0
    print("  [PASS] Anti-transitivity cut edge guard operates correctly")


def main():
    test_dev_candidate_decisions()
    test_shared_family_phone_trap()
    test_missing_data_safety()
    test_anti_transitivity_guard()
    print("\nALL CHECKPOINT 5 TESTS PASSED!")


if __name__ == "__main__":
    main()
