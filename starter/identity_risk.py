"""Quasi-identifier risk evaluation and k-anonymity analyzer for patient cohorts.

Standard-library only module to measure equivalence class distributions across
quasi-identifiers (e.g., age_band, gender, hospital/facility).
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def compute_quasi_identifier_risk(
    records: list[dict[str, Any]],
    min_group_size: int = 5,
    qi_fields: tuple[str, ...] = ("age_band", "gender"),
) -> dict[str, Any]:
    """Analyze k-anonymity risk for a collection of records.

    Parameters
    ----------
    records : list[dict[str, Any]]
        Extracted patient records or dictionaries containing quasi-identifiers.
    min_group_size : int, default 5
        The minimum equivalence class size required for k-anonymity (k >= min_group_size).
    qi_fields : tuple[str, ...], default ("age_band", "gender")
        Quasi-identifier fields to combine into equivalence classes.

    Returns
    -------
    dict[str, Any]
        Dictionary of k-anonymity metrics:
        - total_records: Total count of evaluated records
        - num_equivalence_classes: Number of distinct QI buckets
        - min_k: Smallest equivalence class size (the k-anonymity level)
        - avg_k: Mean equivalence class size
        - at_risk_records_count: Count of records where class size < min_group_size
        - at_risk_records_pct: Percentage of records where class size < min_group_size
        - safe_records_pct: Percentage of records where class size >= min_group_size
        - rare_classes: Map of QI keys to counts for classes with size < min_group_size
    """
    if not records:
        return {
            "total_records": 0,
            "num_equivalence_classes": 0,
            "min_k": 0,
            "avg_k": 0.0,
            "at_risk_records_count": 0,
            "at_risk_records_pct": 0.0,
            "safe_records_pct": 100.0,
            "rare_classes": {},
        }

    classes: Counter[tuple[str, ...]] = Counter()
    for r in records:
        key = tuple(str(r.get(f) or "UNKNOWN").strip().lower() for f in qi_fields)
        classes[key] += 1

    total = len(records)
    num_classes = len(classes)
    counts = list(classes.values())
    min_k = min(counts) if counts else 0
    avg_k = round(total / num_classes, 2) if num_classes else 0.0

    rare_classes = {
        "-".join(k): count
        for k, count in classes.items()
        if count < min_group_size
    }
    at_risk_count = sum(rare_classes.values())
    at_risk_pct = round((at_risk_count / total) * 100, 2) if total else 0.0
    safe_pct = round(100.0 - at_risk_pct, 2)

    return {
        "total_records": total,
        "num_equivalence_classes": num_classes,
        "min_k": min_k,
        "avg_k": avg_k,
        "at_risk_records_count": at_risk_count,
        "at_risk_records_pct": at_risk_pct,
        "safe_records_pct": safe_pct,
        "rare_classes": rare_classes,
    }


def is_k_anonymous(
    records: list[dict[str, Any]],
    k: int = 5,
    qi_fields: tuple[str, ...] = ("age_band", "gender"),
) -> bool:
    """Return True if all equivalence classes have at least k records."""
    risk = compute_quasi_identifier_risk(records, min_group_size=k, qi_fields=qi_fields)
    return risk["min_k"] >= k
