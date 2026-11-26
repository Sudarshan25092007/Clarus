"""Deterministic HMAC pseudonym assignment for linked patient clusters.

Features:
  * Canonical cluster anchoring on immutable record locators (source_file::record_loc)
    rather than mutable PII attributes (names/phones).
  * HMAC-SHA256 keyed hashing producing standard 'PSN-XXXXXX' identifiers.
  * Explicit collision avoidance guard: re-hashes deterministically with '#1', '#2'
    suffixes if a 6-character hex digest collision ever occurs.
  * Explicit secret_key: bytes parameter with no hardcoded secrets.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any


def make_cluster_anchor(records: list[dict], cluster: list[int]) -> str:
    """Generate a canonical, immutable cluster key from record locations."""
    locators = [
        f"{records[i]['source_file']}::{records[i]['record_loc']}"
        for i in cluster
    ]
    return "|".join(sorted(locators))


def generate_pseudonym(anchor: str, secret_key: bytes, attempt: int = 0) -> str:
    """Compute HMAC-SHA256 digest formatted as PSN-XXXXXX."""
    key_text = anchor if attempt == 0 else f"{anchor}#{attempt}"
    digest = hmac.new(secret_key, key_text.encode("utf-8"), hashlib.sha256).hexdigest()
    code = digest[:6].upper()
    return f"PSN-{code}"


def assign_pseudonyms(
    records: list[dict],
    clusters: list[list[int]],
    secret_key: bytes,
) -> dict[tuple[str, str], str]:
    """Assign stable, non-reversible pseudonyms to records by cluster.

    Args:
      records: List of canonical patient records.
      clusters: List of clusters, where each cluster is a list of record indices.
      secret_key: Secret key bytes for HMAC-SHA256 (from environment/CLI).

    Returns:
      Mapping of (source_file, record_loc) -> "PSN-XXXXXX"
    """
    if not secret_key:
        raise ValueError("secret_key must be non-empty bytes")

    used_ids: set[str] = set()
    mapping: dict[tuple[str, str], str] = {}

    for cluster in clusters:
        if not cluster:
            continue

        anchor = make_cluster_anchor(records, cluster)

        # Generate unique pseudonym with deterministic collision resolution
        attempt = 0
        psn_id = generate_pseudonym(anchor, secret_key, attempt)
        while psn_id in used_ids:
            attempt += 1
            psn_id = generate_pseudonym(anchor, secret_key, attempt)

        used_ids.add(psn_id)

        # Map each record location in the cluster to the assigned pseudonym
        for idx in cluster:
            rec = records[idx]
            key = (rec["source_file"], rec["record_loc"])
            mapping[key] = psn_id

    return mapping
