"""Candidate generation and blocking for patient record linkage.

Determines which pairs of patient records are plausible candidates for matching,
avoiding all-pairs comparison (O(N^2)) while guaranteeing high candidate recall.

Blocking Rules:
  1. Exact normalized phone: High precision candidate signal.
  2. Normalized name + gender: Surfaces records with missing or omitted phones.
  3. Relative name + relation + gender: Catches name variants/typos where guardian is identical.

Safety Invariants:
  * Never blocks on UHID alone (UHIDs are hospital-scoped, not global).
  * Missing/null values never form shared blocks.
  * Candidate generation only PROPOSES pairs; it does NOT decide identity.
  * Deduplicates candidate pairs so each record pair is evaluated at most once.
  * Input records are never mutated.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def get_candidate_pair_indices(records: list[dict]) -> list[tuple[int, int]]:
    """Return deduplicated list of index pairs (i, j) where i < j.

    Each pair shares at least one non-null blocking key.
    """
    blocks: dict[tuple, list[int]] = defaultdict(list)

    for idx, r in enumerate(records):
        # Key 1: Exact normalized phone (10-digit)
        phone = r.get("phone")
        if phone and isinstance(phone, str) and phone.strip():
            blocks[("phone", phone.strip())].append(idx)

        # Key 2: Normalized name + gender
        name = r.get("name")
        gender = r.get("gender")
        if name and gender and isinstance(name, str) and isinstance(gender, str):
            blocks[("name_gender", name.strip(), gender.strip())].append(idx)

        # Key 3: Relative name + relation + gender
        rel_name = r.get("relative_name")
        rel_type = r.get("relative_relation")
        if (
            rel_name
            and rel_type
            and gender
            and isinstance(rel_name, str)
            and isinstance(rel_type, str)
            and isinstance(gender, str)
        ):
            blocks[("rel_info", rel_name.strip(), rel_type.strip(), gender.strip())].append(idx)

    # Collect unique pairs (i, j) with i < j
    candidate_pairs: set[tuple[int, int]] = set()
    for block_key, member_indices in blocks.items():
        if len(member_indices) > 1:
            for i in range(len(member_indices)):
                for j in range(i + 1, len(member_indices)):
                    idx_a = member_indices[i]
                    idx_b = member_indices[j]
                    if idx_a != idx_b:
                        candidate_pairs.add((min(idx_a, idx_b), max(idx_a, idx_b)))

    return sorted(candidate_pairs)


def generate_candidate_pairs(records: list[dict]) -> list[tuple[dict, dict]]:
    """Generate candidate record pairs for identity linkage.

    Takes a list of canonical patient record dicts (as produced by identity adapters).
    Returns a deduplicated list of 2-tuples (record_a, record_b).

    Guarantees:
      - Does not modify input records.
      - Pairs are deduplicated.
      - Never pairs a record with itself.
      - Missing values never produce candidate pairs.
    """
    pair_indices = get_candidate_pair_indices(records)
    return [(records[i], records[j]) for (i, j) in pair_indices]
