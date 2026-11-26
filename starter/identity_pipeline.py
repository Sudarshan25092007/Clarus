"""Identity Linkage Pipeline Orchestrator.

Thin orchestrator connecting extraction, blocking, pairwise features,
constrained decision clustering, and deterministic pseudonymization into
a single batch entrypoint for main.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from identity_adapters import extract_all_registration_records
from identity_linker import get_candidate_pair_indices
from identity_features import compare_records
from identity_decision import build_candidate_edge
from identity_clustering import cluster_records
from identity_pseudonym import assign_pseudonyms
from identity_clinical_linker import propagate_clinical_pseudonyms


def build_pseudonym_map(
    input_dir: Path,
    secret_key: bytes,
) -> dict[tuple[str, str], str]:
    """Execute end-to-end identity resolution batch across input files.

    Steps:
      1. Extract all canonical patient registration records.
      2. Generate candidate pairs via 3-key blocking.
      3. Compute pairwise features & decision edges (MATCH/NON_MATCH/UNCERTAIN).
      4. Run Score-Ordered Constrained Union-Find clustering with lazy cross-pair verification.
      5. Assign deterministic keyed HMAC pseudonyms (PSN-XXXXXX).
      6. Propagate registration clusters across clinical and unstructured files (Checkpoint 8).

    Args:
      input_dir: Path to the dataset input root directory (e.g. dev/input).
      secret_key: Secret key bytes (from RAKSHA_SECRET).

    Returns:
      Mapping from (source_file, record_loc) -> "PSN-XXXXXX"
    """
    if not secret_key:
        raise ValueError("secret_key must be non-empty bytes from RAKSHA_SECRET")

    # Locate registration directory
    reg_dir = input_dir / "registration" if (input_dir / "registration").is_dir() else input_dir
    records = extract_all_registration_records(reg_dir)
    if not records:
        return {}

    # 1. Candidate Generation / Blocking
    pair_indices = get_candidate_pair_indices(records)

    # 2. Pairwise feature extraction and decision classification
    candidate_edges = [
        build_candidate_edge(i, j, compare_records(records[i], records[j]))
        for i, j in pair_indices
    ]

    # 3. Constrained Union-Find clustering with anti-transitivity verification
    clustering_res = cluster_records(records, candidate_edges)
    clusters = clustering_res["clusters"]

    # 4. Deterministic HMAC pseudonym assignment on registration clusters
    reg_psn_map = assign_pseudonyms(records, clusters, secret_key)

    # 5. Clinical and unstructured propagation across remaining files (Checkpoint 8)
    full_psn_map = propagate_clinical_pseudonyms(input_dir, records, reg_psn_map)
    return full_psn_map
