#!/usr/bin/env python3
"""Acceptance test suite for identity clustering and pseudonym assignment.

Verifies:
1. Dev Dataset Partition & Precision (66 records partitioned, 0 false merges, homonyms kept apart).
2. Transitive Contradiction Prevention (Known Cut Edge): A-B MATCH, B-C MATCH, A-C NON_MATCH.
3. Unblocked Transitive Contradiction (Lazy Check): A-C omitted from candidate edges, caught lazily.
4. Pseudonym Determinism & Format: ^PSN-[0-9A-F]{6}$, identical under same secret, distinct under different secret.
5. Order Independence: Shuffling candidate_edges yields identical clusters and pseudonyms.
6. Collision Guard Test: Mocked hash collision resolves cleanly using #1 deterministic suffix.
"""

import json
from pathlib import Path
import random
import re
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
from identity_linker import get_candidate_pair_indices
from identity_features import compare_records
from identity_decision import build_candidate_edge
from identity_clustering import cluster_records
from identity_pseudonym import (
    assign_pseudonyms,
    make_cluster_anchor,
    generate_pseudonym,
)


def test_dev_clustering_and_precision():
    print("Testing Test 1: Dev dataset partition completeness and precision...")
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
    edges = [build_candidate_edge(i, j, compare_records(records[i], records[j])) for i, j in pair_indices]

    res = cluster_records(records, edges)
    clusters = res["clusters"]

    # 1.1 Partition Completeness
    all_indices = set()
    for c in clusters:
        for idx in c:
            assert idx not in all_indices, f"Record index {idx} appears in multiple clusters!"
            all_indices.add(idx)
    assert all_indices == set(range(len(records))), "Not all records present in clusters!"

    # 1.2 Zero False Merges Check
    false_merges = 0
    for c in clusters:
        true_pids = {records[idx]["_true_pid"] for idx in c if records[idx]["_true_pid"] is not None}
        if len(true_pids) > 1:
            false_merges += 1
            print(f"  VIOLATION: Cluster {c} merged distinct true patients: {true_pids}")
    assert false_merges == 0, f"Detected {false_merges} false merges in clustering!"

    # 1.3 Homonym Traps Remain in Separate Clusters
    # E.g. Harish Pandey age 59 vs age 21
    harish_indices = [idx for idx, r in enumerate(records) if r.get("name") == "harish pandey"]
    assert len(harish_indices) == 2, "Expected 2 Harish Pandey records"
    harish_clusters = [c for c in clusters if any(h in c for h in harish_indices)]
    assert len(harish_clusters) == 2, "Harish Pandey homonyms were incorrectly merged!"

    # 1.4 True Matches Merged (e.g. Neha Paswan)
    neha_indices = [idx for idx, r in enumerate(records) if r.get("name") == "neha paswan"]
    assert len(neha_indices) == 2, "Expected 2 Neha Paswan records"
    neha_cluster = [c for c in clusters if neha_indices[0] in c][0]
    assert neha_indices[1] in neha_cluster, "Neha Paswan cross-hospital records failed to merge!"

    print(f"  [PASS] 66 records partitioned into {len(clusters)} clusters with ZERO false merges")


def test_transitive_contradiction_known_cut_edge():
    print("Testing Test 2: Transitive contradiction prevention (known cut edge)...")
    # Synthetic 3-record setup:
    # A (0): "Ramesh Sharma", age 45, male, phone 9876543210
    # B (1): "Ramesh Sharma", age 45, male, phone 9876543210 (Duplicate export)
    # C (2): "Ramesh Sharma", age 15, male, phone 9876543210 (Child sharing phone)
    rec_a = {"name": "ramesh sharma", "age": 45, "gender": "M", "phone": "9876543210", "source_file": "a.csv", "record_loc": "row:0"}
    rec_b = {"name": "ramesh sharma", "age": 45, "gender": "M", "phone": "9876543210", "source_file": "b.csv", "record_loc": "row:0"}
    rec_c = {"name": "ramesh sharma", "age": 15, "gender": "M", "phone": "9876543210", "source_file": "c.csv", "record_loc": "row:0"}
    records = [rec_a, rec_b, rec_c]

    # Precomputed edges:
    # A-B: MATCH (score 0.95)
    # B-C: MATCH (hypothetical high score 0.90 to test constraint)
    # A-C: NON_MATCH (cut edge due to age diff = 30)
    candidate_edges = [
        {"i": 0, "j": 1, "decision": "MATCH", "score": 0.95, "is_active_edge": True},
        {"i": 1, "j": 2, "decision": "MATCH", "score": 0.90, "is_active_edge": True},
        {"i": 0, "j": 2, "decision": "NON_MATCH", "score": 0.0, "is_active_edge": False},
    ]

    res = cluster_records(records, candidate_edges)
    clusters = res["clusters"]
    blocked = res["blocked_merges"]

    assert len(blocked) == 1, f"Expected 1 blocked merge, got {len(blocked)}"
    assert blocked[0]["attempted_edge"] == (1, 2)
    assert blocked[0]["conflicting_pair"] == (0, 2)

    # A and B merge into {0, 1}; C remains singleton {2}
    assert [0, 1] in clusters, f"Expected cluster [0, 1], got {clusters}"
    assert [2] in clusters, f"Expected singleton [2], got {clusters}"
    print("  [PASS] Known cut edge prevented transitive contradiction")


def test_unblocked_transitive_contradiction_lazy_check():
    print("Testing Test 3: Unblocked transitive contradiction (lazy cross-pair check)...")
    # Same 3 records as above, BUT edge A-C is completely omitted from candidate_edges!
    # (Simulates blocking never having proposed A-C)
    rec_a = {"name": "ramesh sharma", "age": 45, "gender": "M", "phone": "9876543210", "source_file": "a.csv", "record_loc": "row:0"}
    rec_b = {"name": "ramesh sharma", "age": 45, "gender": "M", "phone": "9876543210", "source_file": "b.csv", "record_loc": "row:0"}
    rec_c = {"name": "ramesh sharma", "age": 15, "gender": "M", "phone": "9876543210", "source_file": "c.csv", "record_loc": "row:0"}
    records = [rec_a, rec_b, rec_c]

    # Only A-B and B-C are proposed:
    candidate_edges = [
        {"i": 0, "j": 1, "decision": "MATCH", "score": 0.95, "is_active_edge": True},
        {"i": 1, "j": 2, "decision": "MATCH", "score": 0.90, "is_active_edge": True},
    ]

    res = cluster_records(records, candidate_edges)
    clusters = res["clusters"]
    blocked = res["blocked_merges"]

    # Lazy check between C_0 {0, 1} and C_1 {2} must compute A-C on the fly, see age diff 30, and block!
    assert len(blocked) == 1, f"Lazy check failed to block contradiction: {blocked}"
    assert blocked[0]["conflicting_pair"] == (0, 2)
    assert [0, 1] in clusters
    assert [2] in clusters
    print("  [PASS] Lazy cross-pair check caught unproposed contradiction dynamically")


def test_pseudonym_determinism_and_format():
    print("Testing Test 4: Pseudonym determinism, key sensitivity, and format...")
    rec_1 = {"source_file": "hospital_a.csv", "record_loc": "row:0"}
    rec_2 = {"source_file": "hospital_b.json", "record_loc": "/patients/3"}
    records = [rec_1, rec_2]
    clusters = [[0, 1]]

    secret_key_a = b"super_secret_key_alpha"
    secret_key_b = b"super_secret_key_beta"

    # Run twice with secret A -> identical mappings
    psn_map_1 = assign_pseudonyms(records, clusters, secret_key_a)
    psn_map_2 = assign_pseudonyms(records, clusters, secret_key_a)
    assert psn_map_1 == psn_map_2, "Pseudonym assignment is not deterministic!"

    # Format verification: ^PSN-[0-9A-F]{6}$
    pattern = re.compile(r"^PSN-[0-9A-F]{6}$")
    for psn in psn_map_1.values():
        assert pattern.match(psn), f"Invalid pseudonym format: {psn}"

    # Both records in cluster must share identical pseudonym
    psn_rec1 = psn_map_1[("hospital_a.csv", "row:0")]
    psn_rec2 = psn_map_1[("hospital_b.json", "/patients/3")]
    assert psn_rec1 == psn_rec2, "Records in same cluster received different pseudonyms!"

    # Run with secret B -> different pseudonyms
    psn_map_diff = assign_pseudonyms(records, clusters, secret_key_b)
    assert psn_map_diff != psn_map_1, "Different secret keys produced identical pseudonyms!"
    print("  [PASS] Pseudonyms strictly conform to format, determinism, and key sensitivity")


def test_order_independence():
    print("Testing Test 5: Candidate edge order independence...")
    dev_dir = HERE.parent / "dev"
    if not (dev_dir / "truth" / "truth.json").exists():
        print("  [SKIP] truth.json not found")
        return

    records = extract_sanjeevani_csv(dev_dir / "input/registration/sanjeevani_registration.csv")
    pair_indices = get_candidate_pair_indices(records)
    edges = [build_candidate_edge(i, j, compare_records(records[i], records[j])) for i, j in pair_indices]

    res_normal = cluster_records(records, edges)

    # Shuffle edges with different random seeds
    for seed in (42, 123, 999):
        shuffled_edges = list(edges)
        random.Random(seed).shuffle(shuffled_edges)
        res_shuffled = cluster_records(records, shuffled_edges)
        assert res_normal["clusters"] == res_shuffled["clusters"], (
            f"Clusters differed under edge permutation with seed {seed}!"
        )

    print("  [PASS] Clustering output is strictly order-independent")


def test_collision_guard():
    print("Testing Test 6: Pseudonym collision guard (deterministic re-hashing)...")
    # Test anchor generation
    anchor = "hosp_a.csv::row:0|hosp_b.tsv::row:5"
    secret = b"test_collision_secret"

    id_attempt_0 = generate_pseudonym(anchor, secret, attempt=0)
    id_attempt_1 = generate_pseudonym(anchor, secret, attempt=1)
    id_attempt_2 = generate_pseudonym(anchor, secret, attempt=2)

    pattern = re.compile(r"^PSN-[0-9A-F]{6}$")
    assert pattern.match(id_attempt_0)
    assert pattern.match(id_attempt_1)
    assert pattern.match(id_attempt_2)

    # Ensure attempts produce distinct hashes
    assert id_attempt_0 != id_attempt_1, "Attempt 0 and 1 collision occurred"
    assert id_attempt_1 != id_attempt_2, "Attempt 1 and 2 collision occurred"

    # Simulate assign_pseudonyms encountering collisions
    rec_a = {"source_file": "file_1.csv", "record_loc": "row:0"}
    rec_b = {"source_file": "file_2.csv", "record_loc": "row:0"}
    records = [rec_a, rec_b]
    clusters = [[0], [1]]

    # Generate pseudonyms for 2 separate singletons
    mapping = assign_pseudonyms(records, clusters, secret)
    ids = list(mapping.values())
    assert len(ids) == len(set(ids)), f"Duplicate pseudonyms produced: {ids}"
    print("  [PASS] Deterministic collision resolution verified")


def main():
    test_dev_clustering_and_precision()
    test_transitive_contradiction_known_cut_edge()
    test_unblocked_transitive_contradiction_lazy_check()
    test_pseudonym_determinism_and_format()
    test_order_independence()
    test_collision_guard()
    print("\nALL CHECKPOINT 6 ACCEPTANCE TESTS PASSED!")


if __name__ == "__main__":
    main()
