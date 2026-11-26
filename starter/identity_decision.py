"""Deterministic decision layer for patient record linkage.

Evaluates pairwise comparison features from `compare_records(a, b)` and assigns
one of three deterministic tiers:
  * MATCH     — High-precision active link edge for clustering.
  * NON_MATCH — Hard contradiction / cut edge (MUST-NOT-LINK constraint).
  * UNCERTAIN — Ambiguous or held-back candidate pair (never forces a merge).

Guarantees:
  * Strict None-safety (explicit `is not None` and boolean checks; zero TypeError risk).
  * High precision first (avoids catastrophic false merges).
  * Anti-transitivity interface for downstream graph clustering.
  * UHID-blind (no hospital ID shortcuts).
"""

from __future__ import annotations

from typing import Any


def decide_pairwise_match(feats: dict[str, Any]) -> dict[str, Any]:
    """Evaluate pairwise features and return a structured decision dictionary.

    Returns:
      {
          "decision": "MATCH" | "NON_MATCH" | "UNCERTAIN",
          "score": float (confidence in match, 0.0 to 1.0),
          "reasons": list[str] (explainable decision triggers)
      }
    """
    reasons: list[str] = []

    gender_agree = feats.get("gender_agreement")
    phone_exact = feats.get("phone_exact")
    age_diff = feats.get("age_difference")
    name_sim = feats.get("name_similarity")
    rel_sim = feats.get("relative_name_similarity")
    rel_agree = feats.get("relation_agreement")
    same_source = feats.get("same_source", False)

    # =========================================================================
    # Tier 1: NON_MATCH (Hard Contradictions / Anti-Link Constraints)
    # =========================================================================

    # 1.1 Explicit gender conflict
    if gender_agree is False:
        reasons.append("gender_conflict")
        return {"decision": "NON_MATCH", "score": 0.0, "reasons": reasons}

    # 1.2 Shared household / family phone trap:
    # Same phone, but age gap (> 5 yrs) indicates family members sharing one mobile
    if (
        phone_exact is True
        and age_diff is not None
        and age_diff > 5
    ):
        reasons.append(f"shared_family_phone_trap: age_gap={age_diff}, name_sim={name_sim}")
        return {"decision": "NON_MATCH", "score": 0.05, "reasons": reasons}

    # 1.3 Hard age incompatibility (> 5 years gap)
    if age_diff is not None and age_diff > 5:
        reasons.append(f"age_incompatible: gap={age_diff}")
        return {"decision": "NON_MATCH", "score": 0.0, "reasons": reasons}

    # 1.4 Phone contradiction with corroborating mismatch:
    # Both phones exist and differ, coupled with age gap or guardian mismatch
    if phone_exact is False:
        if age_diff is not None and age_diff > 3:
            reasons.append(f"phone_conflict_with_age_gap: gap={age_diff}")
            return {"decision": "NON_MATCH", "score": 0.05, "reasons": reasons}
        if (
            rel_sim is not None
            and rel_sim < 0.65
            and name_sim is not None
            and name_sim < 0.75
        ):
            reasons.append("phone_conflict_with_name_and_relative_mismatch")
            return {"decision": "NON_MATCH", "score": 0.05, "reasons": reasons}

    # 1.5 Conflicting relative / guardian:
    # Both records have distinct relative names (< 0.50) without phone match or with age gap
    if rel_sim is not None and rel_sim < 0.50:
        if (age_diff is not None and age_diff > 3) or phone_exact is False:
            reasons.append(f"conflicting_relative: rel_sim={rel_sim}")
            return {"decision": "NON_MATCH", "score": 0.10, "reasons": reasons}

    # =========================================================================
    # Tier 2: MATCH (High-Precision Positive Links)
    # =========================================================================

    # 2.1 Strong phone match with corroborating identity
    if phone_exact is True and gender_agree is not False:
        if age_diff is None or age_diff <= 3:
            has_name_corrob = name_sim is not None and name_sim >= 0.80
            has_rel_corrob = rel_sim is not None and rel_sim >= 0.80
            if has_name_corrob or has_rel_corrob:
                reasons.append("phone_exact_with_corroboration")
                score = 0.95 if (name_sim is not None and name_sim >= 0.90) else 0.88
                if age_diff is not None and age_diff <= 1:
                    score = min(0.99, score + 0.03)
                return {"decision": "MATCH", "score": round(score, 3), "reasons": reasons}

    # 2.2 Same-source duplicate export with strong agreement
    if same_source is True and gender_agree is not False:
        if (
            name_sim is not None
            and name_sim >= 0.85
            and (age_diff is None or age_diff <= 2)
            and phone_exact is not False
        ):
            reasons.append("same_source_duplicate")
            return {"decision": "MATCH", "score": 0.92, "reasons": reasons}

    # 2.3 Multi-attribute consensus without phone (missing phone on one/both records)
    if (
        phone_exact is None
        and gender_agree is True
        and age_diff is not None
        and age_diff <= 2
        and name_sim is not None
        and name_sim >= 0.85
        and rel_sim is not None
        and rel_sim >= 0.85
        and rel_agree is True
    ):
        reasons.append("multi_attribute_consensus_without_phone")
        return {"decision": "MATCH", "score": 0.86, "reasons": reasons}

    # =========================================================================
    # Tier 3: UNCERTAIN (Held Back for Conservative Handling / Review)
    # =========================================================================

    if phone_exact is True:
        reasons.append("phone_exact_but_insufficient_or_cross_script_name_corroboration")
        score = 0.55
    elif name_sim is not None and name_sim >= 0.80:
        reasons.append("name_agree_but_missing_phone_or_relative_corroboration")
        score = 0.45
    else:
        reasons.append("insufficient_evidence")
        score = 0.25

    return {"decision": "UNCERTAIN", "score": score, "reasons": reasons}


# =============================================================================
# Graph Clustering Interfaces (Anti-Transitivity & Edge Structs)
# =============================================================================

def build_candidate_edge(idx_a: int, idx_b: int, feats: dict[str, Any]) -> dict[str, Any]:
    """Package pairwise decision into a graph edge structure for clustering."""
    res = decide_pairwise_match(feats)
    return {
        "i": min(idx_a, idx_b),
        "j": max(idx_a, idx_b),
        "decision": res["decision"],
        "score": res["score"],
        "reasons": res["reasons"],
        "is_active_edge": res["decision"] == "MATCH",
        "is_cut_edge": res["decision"] == "NON_MATCH",
    }


def find_cluster_contradictions(
    cluster: set[int] | list[int],
    cut_edges: set[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Check a proposed cluster against known cut edges (anti-transitivity guard).

    If any pair (u, v) in the cluster is in cut_edges (i.e. NON_MATCH),
    merging this cluster would violate a hard contradiction.

    Returns:
      List of conflicting (u, v) pairs found within the cluster.
    """
    cluster_list = sorted(cluster)
    contradictions = []
    for i in range(len(cluster_list)):
        for j in range(i + 1, len(cluster_list)):
            u, v = cluster_list[i], cluster_list[j]
            if (u, v) in cut_edges or (v, u) in cut_edges:
                contradictions.append((u, v))
    return contradictions
