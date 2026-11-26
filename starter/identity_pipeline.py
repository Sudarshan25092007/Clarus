"""Identity linkage pipeline orchestrator.

Coordinates extraction, candidate generation, pairwise feature comparison,
deterministic decision logic, constrained graph clustering, and HMAC pseudonym
assignment.

Exposes a clean single entrypoint:
    build_pseudonym_map(input_dir: Path, secret_key: bytes) -> dict[tuple[str, str], str]
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from identity_adapters import (
    extract_all_registration_records,
    extract_sanjeevani_csv,
    extract_jeevanjyoti_tsv,
    extract_dhh_json,
    extract_arogya_legacy_csv,
)
from identity_linker import get_candidate_pair_indices
from identity_features import compare_records
from identity_decision import build_candidate_edge
from identity_clustering import cluster_records
from identity_pseudonym import assign_pseudonyms


def extract_registration_records_from_input(input_dir: Path) -> list[dict]:
    """Extract canonical registration records from an input directory.

    1. Checks input_dir / "registration" if present.
    2. Scans input_dir directly.
    3. Recursively scans input_dir for matching registration files as fallback.
    """
    input_path = Path(input_dir)
    reg_dir = input_path / "registration"
    if reg_dir.is_dir():
        records = extract_all_registration_records(reg_dir)
        if records:
            return records

    records = extract_all_registration_records(input_path)
    if records:
        return records

    # Recursive fallback for non-standard folder layouts
    for cand in input_path.rglob("*"):
        if cand.is_file():
            nl = cand.name.lower()
            if "sanjeevani_registration" in nl and cand.suffix == ".csv":
                records.extend(extract_sanjeevani_csv(cand))
            elif "jeevanjyoti" in nl and cand.suffix == ".tsv":
                records.extend(extract_jeevanjyoti_tsv(cand))
            elif "dhh" in nl and cand.suffix == ".json":
                records.extend(extract_dhh_json(cand))
            elif "arogya" in nl and cand.suffix == ".csv":
                records.extend(extract_arogya_legacy_csv(cand))

    return records


def build_pseudonym_map(
    input_dir: Path,
    secret_key: bytes,
) -> dict[tuple[str, str], str]:
    """Execute end-to-end identity linkage and return a locator-to-pseudonym map.

    Steps:
      1. Extract all canonical registration records from input_dir.
      2. Generate candidate pairs (identity_linker).
      3. Compute pairwise features (identity_features) & decision edges (identity_decision).
      4. Run constrained clustering (identity_clustering).
      5. Assign deterministic HMAC pseudonyms (identity_pseudonym).

    Returns:
      {(source_file, record_loc): "PSN-XXXXXX"}
    """
    if not secret_key:
        raise ValueError("secret_key must be non-empty bytes (set RAKSHA_SECRET)")

    records = extract_registration_records_from_input(input_dir)
    if not records:
        return {}

    # 1. Candidate blocking
    pair_indices = get_candidate_pair_indices(records)

    # 2. Pairwise features & decision edges
    candidate_edges = [
        build_candidate_edge(i, j, compare_records(records[i], records[j]))
        for i, j in pair_indices
    ]

    # 3. Score-ordered constrained graph clustering (anti-transitivity enforced)
    cluster_res = cluster_records(records, candidate_edges)
    clusters = cluster_res["clusters"]

    # 4. Assign deterministic HMAC pseudonyms
    return assign_pseudonyms(records, clusters, secret_key)
