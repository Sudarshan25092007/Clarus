"""Identity extraction and normalization for patient records.

Converts raw patient fields from heterogeneous hospital formats into a
single consistent internal representation.  This module extracts and
normalizes identity evidence.  It does NOT decide whether two records
belong to the same person — that is the job of the matching layer.

Supported normalization (current scope):
  * name  — lowercase, collapsed whitespace, stripped punctuation noise
  * age   — integer from "47", "47 years", "47 वर्ष", "48/F", etc.
  * gender — "M", "F", or None from English and Hindi labels
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# name normalization
# ---------------------------------------------------------------------------

def normalize_name(raw: Any) -> str | None:
    """Lowercase, collapse whitespace, strip trailing/leading junk.

    Returns None for empty / missing / clearly non-name input.
    Does NOT transliterate or do fuzzy matching — that belongs in the
    matching layer.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip()
    if not text:
        return None

    # Lowercase (safe for Latin; Devanagari has no case, so this is a no-op)
    text = text.lower()

    # Remove surrounding quotes if present
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        text = text[1:-1].strip()

    # Collapse multiple whitespace characters to single space
    text = re.sub(r"\s+", " ", text)

    # Strip trailing/leading punctuation that isn't part of a name
    # Keep hyphens and dots inside names (e.g. "al-amin", "M.D.")
    text = text.strip(" \t\n\r,;:|")

    if not text:
        return None

    return text


# ---------------------------------------------------------------------------
# age normalization
# ---------------------------------------------------------------------------

# Matches digits optionally followed by Hindi "वर्ष" (years) or English
# text, or the "Age/Gender" pattern like "48/F".
_AGE_PATTERN = re.compile(
    r"(\d{1,3})"                  # 1-3 digit number
    r"(?:\s*(?:वर्ष|years?|yrs?|yr))?"  # optional unit suffix
    r"(?:\s*/\s*[MFmf])?"        # optional /Gender suffix
)


def normalize_age(raw: Any) -> int | None:
    """Extract an integer age from various formats.

    Handles:
      "47"        → 47
      "47 years"  → 47
      "47 वर्ष"   → 47
      "48/F"      → 48
      "0"         → 0   (infants are valid)
      ""          → None
      None        → None
      95          → 95   (already an int)
      "unknown"   → None

    Returns None when no plausible age can be extracted.
    Rejects ages > 150 as implausible.
    """
    if raw is None:
        return None

    # Already numeric
    if isinstance(raw, (int, float)):
        age = int(raw)
        return age if 0 <= age <= 150 else None

    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip()
    if not text:
        return None

    m = _AGE_PATTERN.search(text)
    if m:
        age = int(m.group(1))
        return age if 0 <= age <= 150 else None

    return None


# ---------------------------------------------------------------------------
# gender normalization
# ---------------------------------------------------------------------------

_MALE_VALUES = frozenset({
    "m", "male", "पुरुष", "पु",
})

_FEMALE_VALUES = frozenset({
    "f", "female", "महिला", "स्त्री", "म",
})


def normalize_gender(raw: Any) -> str | None:
    """Map gender to 'M', 'F', or None.

    Handles English (M, F, Male, Female) and Hindi (पुरुष, महिला).
    Returns None for missing, ambiguous or unrecognized values.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip().lower()
    if not text:
        return None

    if text in _MALE_VALUES:
        return "M"
    if text in _FEMALE_VALUES:
        return "F"

    # Handle age/gender combos like "48/F" or "48 Yrs/M"
    gender_suffix = re.search(r"/\s*([mfMF])\s*$", raw.strip())
    if gender_suffix:
        return gender_suffix.group(1).upper()

    return None


# ---------------------------------------------------------------------------
# phone normalization
# ---------------------------------------------------------------------------

_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Matches common phone label prefixes like "Mob:", "Mobile:", "Phone:", etc.
_PHONE_LABEL_RE = re.compile(
    r"^(?i:\s*(?:mob(?:ile)?|phone|ph|tel|contact|दूरभाष|मोबाइल)\s*[:.-]?\s*)"
)


def normalize_phone(raw: Any) -> str | None:
    """Normalize Indian mobile numbers to standard 10-digit format.

    Policy:
      1. Accepts integer or string inputs. Rejects non-integer floats.
      2. Strips common label prefixes (e.g. 'Mob:', 'Phone:', 'मोबाइल:').
      3. Rejects strings containing non-label alphabetic characters (e.g. 'UHID-9876543210').
      4. Translates Devanagari numerals ('०१२३४५६७८९' -> '0123456789').
      5. Strips non-digit formatting characters (spaces, hyphens, plus, parens, dots).
      6. Prefix resolution:
         - 10 digits: [6-9]xxxxxxxxx -> accepted directly.
         - 11 digits with leading 0: 0[6-9]xxxxxxxxx -> standard Indian STD trunk dialling
           convention. NOTE: This is a dataset-backed normalization rule (empirically verified
           in Jeevanjyoti TSV matching Sanjeevani CSV for Neha Paswan, e.g. '096522-93109' ==
           '9652293109'). It is NOT applied universally; the subsequent 10 digits must begin with
           a valid mobile series (6..9) and pass plausibility checks.
         - 12 digits: 91[6-9]xxxxxxxxx -> Indian international country code prefix (+91).
         - 13 digits: 910[6-9]xxxxxxxxx -> country code + trunk dialling (+91 (0)...).
         - 14 digits: 0091[6-9]xxxxxxxxx -> international exit code + 91 (+0091 ...).
      7. Plausibility checks:
         - Candidate must start with 6, 7, 8, or 9 (TRAI mobile number series).
         - Rejects dummy numbers where all digits are identical (e.g. '9999999999', '0000000000').
         - Rejects numbers failing length requirements (e.g. '12345', 12-digit Aadhaar).
    """
    if raw is None:
        return None

    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not raw.is_integer():
            return None
        raw = str(int(raw))

    if not isinstance(raw, str):
        raw = str(raw)

    text = raw.strip()
    if not text:
        return None

    # Strip known telephone label prefix
    cleaned = _PHONE_LABEL_RE.sub("", text)

    # If alphabetic characters remain, this is a non-phone identifier (e.g. "UHID-9876543210")
    if re.search(r"[a-zA-Z]", cleaned):
        return None

    # Translate Devanagari numerals
    cleaned = cleaned.translate(_DEVA_DIGITS)

    # Extract digits only
    digits = re.sub(r"\D", "", cleaned)

    candidate = None
    if len(digits) == 10 and digits[0] in "6789":
        candidate = digits
    elif len(digits) == 11 and digits[0] == "0" and digits[1] in "6789":
        candidate = digits[1:]
    elif len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        candidate = digits[2:]
    elif len(digits) == 13 and digits.startswith("910") and digits[3] in "6789":
        candidate = digits[3:]
    elif len(digits) == 14 and digits.startswith("0091") and digits[4] in "6789":
        candidate = digits[4:]

    if candidate is None:
        return None

    # Plausibility check: reject dummy numbers with all identical digits
    if len(set(candidate)) <= 1:
        return None

    return candidate


# ---------------------------------------------------------------------------
# relative name & relation normalization
# ---------------------------------------------------------------------------

_RELATION_MAP = {
    "s/o": "S/o",
    "so": "S/o",
    "son of": "S/o",
    "बेटा": "S/o",
    "पुत्र": "S/o",
    "d/o": "D/o",
    "do": "D/o",
    "daughter of": "D/o",
    "बेटी": "D/o",
    "पुत्री": "D/o",
    "w/o": "W/o",
    "wo": "W/o",
    "wife of": "W/o",
    "पत्नी": "W/o",
    "h/o": "H/o",
    "ho": "H/o",
    "husband of": "H/o",
    "पति": "H/o",
    "c/o": "C/o",
    "co": "C/o",
    "care of": "C/o",
    "f/o": "F/o",
    "father of": "F/o",
    "पिता": "F/o",
    "m/o": "M/o",
    "mother of": "M/o",
    "माता": "M/o",
}

_RELATION_PREFIX_RE = re.compile(
    r"^(?i:(?:s/o|d/o|w/o|h/o|c/o|son\s+of|daughter\s+of|wife\s+of|husband\s+of|care\s+of)\b|(?:पिता|पति|पत्नी|माता|बेटा|बेटी))[\s:.-]*"
)


def split_relation_and_name(raw: Any) -> tuple[str | None, str | None]:
    """Split relative string into (canonical_relation, clean_relative_name).

    Prevents relation prefixes from corrupting the relative name.
    E.g.
      ' S/o   Zubair '    -> ('S/o', 'zubair')
      'W/O: Ramesh Kumar' -> ('W/o', 'ramesh kumar')
      'पिता: रमेश कुमार'  -> ('F/o', 'रमेश कुमार')
      'Naresh Chauhan'    -> (None, 'naresh chauhan')
      'D/o किशन निषाद'    -> ('D/o', 'किशन निषाद')
    """
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    m = _RELATION_PREFIX_RE.match(text)
    if m:
        key = m.group(0).rstrip(" :.-").lower()
        rel = _RELATION_MAP.get(key)
        name_part = text[m.end():].strip(" :.-")
        clean_name = normalize_name(name_part)
        return rel, clean_name
    return None, normalize_name(text)


def normalize_relative_name(raw: Any, strip_prefix: bool = True) -> str | None:
    """Normalize relative's name string, optionally stripping relation prefixes.

    Default strips relation prefixes so the relative name is purely the relative's name
    (e.g. 's/o zubair' -> 'zubair').
    """
    if strip_prefix:
        _, name = split_relation_and_name(raw)
        return name
    return normalize_name(raw)


def normalize_relation(raw: Any) -> str | None:
    """Normalize relationship type (S/o, D/o, W/o, H/o, C/o, F/o, M/o)."""
    if raw is None:
        return None
    text = str(raw).strip().lower().rstrip(".:;,")
    if not text:
        return None
    return _RELATION_MAP.get(text, str(raw).strip())


# ---------------------------------------------------------------------------
# combined normalizer
# ---------------------------------------------------------------------------

def normalize_identity(record: dict) -> dict:
    """Normalize raw patient fields into a consistent internal form.

    Input:  a dict with raw field values, keyed however the source
            format provides them. The caller is responsible for mapping
            source-specific keys to the canonical keys before calling.

    Output: a dict with normalized fields. Missing or unrecoverable
            fields are None, never fabricated.

    Canonical representation guarantees:
      - name: clean patient name
      - age: integer age or None
      - gender: 'M', 'F', or None
      - phone: 10-digit mobile string or None
      - relative_name: clean relative name (prefixes like 'S/o' stripped)
      - relative_relation: canonical relation type ('S/o', 'W/o', 'D/o', etc.)
      - uhid, village, source_file, record_loc: preserved cleanly
    """
    rel_raw = record.get("relative_name")
    rel_inferred, rel_clean_name = split_relation_and_name(rel_raw)

    rel_explicit = normalize_relation(record.get("relative_relation"))
    final_rel = rel_explicit or rel_inferred

    return {
        "name": normalize_name(record.get("name")),
        "age": normalize_age(record.get("age")),
        "gender": normalize_gender(record.get("gender")),
        "phone": normalize_phone(record.get("phone")),
        "relative_name": rel_clean_name,
        "relative_relation": final_rel,
        # Separate hospital-specific and metadata fields
        "uhid": record.get("uhid"),
        "village": record.get("village"),
        "source_file": record.get("source_file"),
        "record_loc": record.get("record_loc"),
    }


