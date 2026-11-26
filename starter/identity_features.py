"""Pairwise feature extraction for patient record comparison.

Extracts similarity and agreement features between two canonical patient records.
Produces a structured feature dictionary for downstream linkage evaluation.
Does NOT make match decisions.

Features:
  * phone_exact: bool | None (True if both phones exist and are identical)
  * name_similarity: float | None (SequenceMatcher ratio 0.0 to 1.0, or None if either missing)
  * gender_agreement: bool | None (True if both genders exist and are equal)
  * age_difference: int | None (Absolute integer difference, or None if either missing)
  * relative_name_similarity: float | None (SequenceMatcher ratio for relative names)
  * relation_agreement: bool | None (True if both relations exist and are equal)
  * village_agreement: bool | None (True if both villages exist and match after normalization)
  * same_source: bool (True if both records originate from the same source file)
"""

from __future__ import annotations

import difflib
from typing import Any


def compare_records(a: dict, b: dict) -> dict[str, Any]:
    """Compare two canonical patient records and extract pairwise similarity features.

    Rules:
      * Missing/null values produce None, never False or 1.0.
      * Two missing phones/names/ages/genders produce None.
      * UHID is never used as a cross-hospital matching feature.
      * Same-source records are flagged via 'same_source' without rejecting duplicates.
    """
    # 1. phone_exact: Both must exist and be non-empty strings
    phone_a = a.get("phone")
    phone_b = b.get("phone")
    if phone_a and phone_b:
        phone_exact: bool | None = (phone_a == phone_b)
    else:
        phone_exact = None

    # 2. name_similarity: SequenceMatcher ratio between normalized names
    name_a = a.get("name")
    name_b = b.get("name")
    if name_a and name_b:
        name_sim: float | None = round(
            difflib.SequenceMatcher(None, name_a, name_b).ratio(), 4
        )
    else:
        name_sim = None

    # 3. gender_agreement: Both must be known ('M' or 'F')
    gender_a = a.get("gender")
    gender_b = b.get("gender")
    if gender_a and gender_b:
        gender_agree: bool | None = (gender_a == gender_b)
    else:
        gender_agree = None

    # 4. age_difference: Absolute integer difference, or None if either missing
    age_a = a.get("age")
    age_b = b.get("age")
    if age_a is not None and age_b is not None:
        age_diff: int | None = abs(int(age_a) - int(age_b))
    else:
        age_diff = None

    # 5. relative_name_similarity: SequenceMatcher ratio for relative names
    rel_name_a = a.get("relative_name")
    rel_name_b = b.get("relative_name")
    if rel_name_a and rel_name_b:
        rel_name_sim: float | None = round(
            difflib.SequenceMatcher(None, rel_name_a, rel_name_b).ratio(), 4
        )
    else:
        rel_name_sim = None

    # 6. relation_agreement: Both must be known (e.g. 'S/o', 'W/o')
    rel_type_a = a.get("relative_relation")
    rel_type_b = b.get("relative_relation")
    if rel_type_a and rel_type_b:
        rel_agree: bool | None = (rel_type_a == rel_type_b)
    else:
        rel_agree = None

    # 7. village_agreement: Case-insensitive normalized string match
    vill_a = a.get("village")
    vill_b = b.get("village")
    if vill_a and vill_b:
        vill_agree: bool | None = (
            vill_a.strip().lower() == vill_b.strip().lower()
        )
    else:
        vill_agree = None

    # 8. same_source: Flag duplicate exports / co-file records
    src_a = a.get("source_file")
    src_b = b.get("source_file")
    same_source: bool = bool(src_a and src_b and src_a == src_b)

    return {
        "phone_exact": phone_exact,
        "name_similarity": name_sim,
        "gender_agreement": gender_agree,
        "age_difference": age_diff,
        "relative_name_similarity": rel_name_sim,
        "relation_agreement": rel_agree,
        "village_agreement": vill_agree,
        "same_source": same_source,
    }
