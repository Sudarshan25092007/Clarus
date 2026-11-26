"""Format-specific patient identity extraction adapters.

Extracts patient identity records from heterogeneous registration file formats
(CSV, TSV, JSON) and converts them into canonical identity dictionaries via
`normalize_identity()`.

Supported formats (current scope):
  * Sanjeevani Registration CSV (UTF-8 / UTF-8-BOM)
  * Jeevanjyoti Registration TSV (UTF-16 LE)
  * District Hospital Hardoi (DHH) Patients JSON (Nested JSON)
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from identity_extractor import normalize_identity


def _get_rel_path(path: Path | str) -> str:
    """Extract a normalized relative path for tracking source provenance."""
    p = Path(path)
    posix = p.as_posix()
    # Normalize path to match standard output structure (e.g. 'registration/...')
    for marker in ("registration/", "opd/", "discharge/", "notes/", "lab/", "fhir/"):
        if marker in posix:
            return posix[posix.index(marker):]
    return p.name


# ---------------------------------------------------------------------------
# Sanjeevani CSV Adapter
# ---------------------------------------------------------------------------

def extract_sanjeevani_csv(path: Path | str) -> list[dict]:
    """Extract and normalize patient identities from Sanjeevani CSV format.

    Preserves row location 'row:0', 'row:1', etc. (excluding header).
    Uses explicit column name lookups.
    """
    p = Path(path)
    raw = p.read_bytes()
    # Handle UTF-8 with or without BOM
    text = raw.decode("utf-8-sig", errors="replace")
    rel_path = _get_rel_path(p)

    records: list[dict] = []
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        return records

    for idx, row in enumerate(reader):
        raw_record = {
            "name": row.get("Patient Name"),
            "age": row.get("Age"),
            "gender": row.get("Gender"),
            "phone": row.get("Mobile"),
            "relative_name": row.get("Relative Name"),
            "relative_relation": row.get("Relation"),
            "uhid": row.get("UHID"),
            "village": row.get("Village/Mohalla"),
            "source_file": rel_path,
            "record_loc": f"row:{idx}",
        }
        records.append(normalize_identity(raw_record))

    return records


# ---------------------------------------------------------------------------
# Jeevanjyoti TSV Adapter
# ---------------------------------------------------------------------------

def extract_jeevanjyoti_tsv(path: Path | str) -> list[dict]:
    """Extract and normalize patient identities from Jeevanjyoti TSV format.

    Handles UTF-16 LE encoding and tab delimiters.
    Preserves row location 'row:0', 'row:1', etc. (excluding header).
    """
    p = Path(path)
    raw = p.read_bytes()
    # Detect / decode UTF-16
    text = raw.decode("utf-16", errors="replace")
    rel_path = _get_rel_path(p)

    records: list[dict] = []
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return records

    reader = csv.DictReader(lines, delimiter="\t")
    if not reader.fieldnames:
        return records

    for idx, row in enumerate(reader):
        raw_record = {
            "name": row.get("Naam"),
            "age": row.get("Umar"),
            "gender": row.get("Ling"),
            "phone": row.get("Mobile No"),
            "relative_name": row.get("Pita/Pati ka Naam"),
            "relative_relation": None,  # Inferred from relative_name by normalizer
            "uhid": row.get("Reg No"),
            "village": row.get("Gaon"),
            "source_file": rel_path,
            "record_loc": f"row:{idx}",
        }
        records.append(normalize_identity(raw_record))

    return records


# ---------------------------------------------------------------------------
# District Hospital Hardoi (DHH) JSON Adapter
# ---------------------------------------------------------------------------

def extract_dhh_json(path: Path | str) -> list[dict]:
    """Extract and normalize patient identities from DHH nested JSON format.

    Safely accesses nested patient, contact, guardian, and address dictionaries.
    Preserves JSON pointer location '/patients/0', '/patients/1', etc.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    rel_path = _get_rel_path(p)

    try:
        data = json.loads(text)
    except Exception:
        return []

    records: list[dict] = []
    patients_list = data.get("patients", [])
    if not isinstance(patients_list, list):
        return records

    for idx, pt in enumerate(patients_list):
        if not isinstance(pt, dict):
            continue

        # Safely extract name (may be string or nested dict with first, last, local)
        name_val = None
        raw_name = pt.get("name")
        if isinstance(raw_name, dict):
            first = (raw_name.get("first") or "").strip()
            last = (raw_name.get("last") or "").strip()
            full = f"{first} {last}".strip()
            name_val = full or raw_name.get("local")
        elif isinstance(raw_name, str):
            name_val = raw_name

        # Safely extract nested contact
        contact_obj = pt.get("contact")
        phone_val = contact_obj.get("mobile") if isinstance(contact_obj, dict) else None

        # Safely extract nested guardian
        guardian_obj = pt.get("guardian")
        rel_name = guardian_obj.get("name") if isinstance(guardian_obj, dict) else None
        rel_type = guardian_obj.get("relation") if isinstance(guardian_obj, dict) else None

        # Safely extract nested address
        addr_obj = pt.get("address")
        village_val = addr_obj.get("village") if isinstance(addr_obj, dict) else None

        raw_record = {
            "name": name_val,
            "age": pt.get("age"),
            "gender": pt.get("gender"),
            "phone": phone_val,
            "relative_name": rel_name,
            "relative_relation": rel_type,
            "uhid": pt.get("uhid"),
            "village": village_val,
            "source_file": rel_path,
            "record_loc": f"/patients/{idx}",
        }
        records.append(normalize_identity(raw_record))

    return records
