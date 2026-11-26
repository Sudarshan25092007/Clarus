"""Clinical & Unstructured Record Propagation (Checkpoint 8).

Propagates verified registration pseudonyms across the remaining clinical files
(billing, OPD, lab, discharge summaries, prescriptions, clinical notes, and FHIR)
using tiered resolution with strict precision guards.

Tiers:
  * Tier 1 — Same-Hospital UHID: Exact (hospital_id, uhid) lookup with age/gender conflict check.
  * Tier 2 — Exact 10-Digit Phone: Unique phone lookup with age/gender conflict check.
  * Tier 3 — Unique Name + Gender + Age: Unique (name, gender) match with age_diff <= 2.
  * Default: None (never guesses on ambiguous or conflicting candidates).
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from identity_extractor import (
    normalize_name,
    normalize_age,
    normalize_gender,
    normalize_phone,
)

_RE_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_RE_UHID = re.compile(
    r"\b(SNJ/\d{2}/\d+|JJH-\d+|UHID-\d+|DHH\d{4}-\d+|DHH-\d+)\b",
    re.IGNORECASE,
)
_RE_AGE_SEX = re.compile(
    r"(\d{1,3})\s*(?:Y(?:rs?|ears?)?|वर्ष)?\s*/\s*([MFO]|male|female|पुरुष|महिला)\b",
    re.IGNORECASE,
)


def normalize_uhid(u: str | None) -> str | None:
    """Normalize hospital-specific UHID to standard uppercase format."""
    if not u or not isinstance(u, str):
        return None
    cleaned = u.strip().upper()
    return cleaned if len(cleaned) >= 4 else None


def get_hospital_id(source_hint: str | None = None, uhid_hint: str | None = None) -> str | None:
    """Infer hospital ID ('snj', 'jjh', 'ass', 'dhh') prioritizing UHID prefix over file path."""
    u = (uhid_hint or "").upper()
    if u.startswith("SNJ"):
        return "snj"
    if u.startswith("JJH"):
        return "jjh"
    if u.startswith("UHID"):
        return "ass"
    if u.startswith("DHH"):
        return "dhh"

    s = (source_hint or "").lower()
    if "sanjeevani" in s or "snj" in s:
        return "snj"
    if "jeevanjyoti" in s or "jjh" in s:
        return "jjh"
    if "arogya" in s or "ass" in s:
        return "ass"
    if "dhh" in s or "hardoi" in s:
        return "dhh"
    return None


def build_anchor_index(
    reg_records: list[dict],
    reg_psn_map: dict[tuple[str, str], str],
) -> dict[str, Any]:
    """Freeze verified registration clusters into an immutable read-only lookup index.

    Returns:
      {
        "by_hospital_uhid": dict[tuple[str, str], set[str]],
        "by_phone": dict[str, set[str]],
        "by_name_gender": dict[tuple[str, str], list[dict]],
        "cluster_meta": dict[str, dict],
      }
    """
    by_hospital_uhid: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_phone: dict[str, set[str]] = defaultdict(set)
    by_name_gender: dict[tuple[str, str], list[dict]] = defaultdict(list)
    cluster_meta: dict[str, dict] = defaultdict(
        lambda: {"ages": set(), "genders": set(), "names": set()}
    )

    for r in reg_records:
        key = (r["source_file"], r["record_loc"])
        psn = reg_psn_map.get(key)
        if not psn:
            continue

        hosp = get_hospital_id(r.get("source_file"), r.get("uhid"))
        uhid = normalize_uhid(r.get("uhid"))
        phone = r.get("phone")
        name = r.get("name")
        age = r.get("age")
        gender = r.get("gender")

        if hosp and uhid:
            by_hospital_uhid[(hosp, uhid)].add(psn)
        if phone:
            by_phone[phone].add(psn)
        if name and gender:
            by_name_gender[(name, gender)].append({"psn": psn, "age": age})

        if age is not None:
            cluster_meta[psn]["ages"].add(age)
        if gender:
            cluster_meta[psn]["genders"].add(gender)
        if name:
            cluster_meta[psn]["names"].add(name)

    return {
        "by_hospital_uhid": dict(by_hospital_uhid),
        "by_phone": dict(by_phone),
        "by_name_gender": dict(by_name_gender),
        "cluster_meta": dict(cluster_meta),
    }


def resolve_clinical_record(
    clinical_rec: dict[str, Any],
    anchor_index: dict[str, Any],
) -> str | None:
    """Resolve a clinical record to a pseudonym using tiered precision-first matching.

    clinical_rec fields:
      * hospital_id: str | None
      * uhid: str | None
      * phone: str | None
      * name: str | None
      * age: int | None
      * gender: str | None
    """
    hosp = clinical_rec.get("hospital_id")
    uhid = normalize_uhid(clinical_rec.get("uhid"))
    phone = clinical_rec.get("phone")
    name = clinical_rec.get("name")
    age = clinical_rec.get("age")
    gender = clinical_rec.get("gender")

    by_hospital_uhid = anchor_index["by_hospital_uhid"]
    by_phone = anchor_index["by_phone"]
    by_name_gender = anchor_index["by_name_gender"]
    cluster_meta = anchor_index["cluster_meta"]

    # -------------------------------------------------------------------------
    # Tier 1 — Same-Hospital UHID
    # -------------------------------------------------------------------------
    if hosp and uhid:
        cands = by_hospital_uhid.get((hosp, uhid), set())
        if len(cands) == 1:
            psn = next(iter(cands))
            meta = cluster_meta.get(psn, {})
            # Contradiction guards
            has_gender_conflict = bool(
                gender and meta.get("genders") and gender not in meta["genders"]
            )
            has_age_conflict = bool(
                age is not None
                and meta.get("ages")
                and all(abs(age - a) > 5 for a in meta["ages"])
            )
            if not has_gender_conflict and not has_age_conflict:
                return psn

    # -------------------------------------------------------------------------
    # Tier 2 — Exact 10-Digit Phone
    # -------------------------------------------------------------------------
    if phone:
        cands = by_phone.get(phone, set())
        if len(cands) == 1:
            psn = next(iter(cands))
            meta = cluster_meta.get(psn, {})
            has_gender_conflict = bool(
                gender and meta.get("genders") and gender not in meta["genders"]
            )
            has_age_conflict = bool(
                age is not None
                and meta.get("ages")
                and all(abs(age - a) > 5 for a in meta["ages"])
            )
            if not has_gender_conflict and not has_age_conflict:
                return psn

    # -------------------------------------------------------------------------
    # Tier 3 — Unique Name + Gender + Age (age_diff <= 2)
    # -------------------------------------------------------------------------
    if name and gender:
        entries = by_name_gender.get((name, gender), [])
        matching_psns = set()
        for e in entries:
            e_age = e.get("age")
            if age is not None and e_age is not None:
                if abs(age - e_age) <= 2:
                    matching_psns.add(e["psn"])
            elif age is None and e_age is None:
                matching_psns.add(e["psn"])

        if len(matching_psns) == 1:
            return next(iter(matching_psns))

    return None


# =============================================================================
# Wave A: Structured Clinical Files (Billing, OPD, Lab CSV/JSON, FHIR)
# =============================================================================

def resolve_wave_a(
    input_dir: Path,
    anchor_index: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """Extract and resolve structured clinical records (Billing, OPD, Lab, FHIR)."""
    resolved: dict[tuple[str, str], str] = {}

    # 1. Billing CSVs
    billing_dir = input_dir / "billing"
    if billing_dir.is_dir():
        for p in sorted(billing_dir.glob("*.csv")):
            rel = f"billing/{p.name}"
            hosp = get_hospital_id(p.name)
            try:
                reader = csv.DictReader(p.read_text(encoding="utf-8-sig", errors="replace").splitlines())
                for idx, row in enumerate(reader):
                    rec = {
                        "hospital_id": hosp or get_hospital_id(uhid_hint=row.get("uhid")),
                        "uhid": row.get("uhid"),
                        "phone": normalize_phone(row.get("phone")),
                        "name": normalize_name(row.get("patient_name")),
                        "age": normalize_age(row.get("age")),
                        "gender": normalize_gender(row.get("gender") or row.get("sex")),
                    }
                    psn = resolve_clinical_record(rec, anchor_index)
                    if psn:
                        resolved[(rel, f"row:{idx}")] = psn
            except Exception:
                continue

    # 2. OPD JSONs
    opd_dir = input_dir / "opd"
    if opd_dir.is_dir():
        for p in sorted(opd_dir.glob("*.json")):
            rel = f"opd/{p.name}"
            hosp = get_hospital_id(p.name)
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                visits = data.get("visits", [])
                for idx, v in enumerate(visits):
                    uhid = v.get("patient_ref")
                    rec = {
                        "hospital_id": hosp or get_hospital_id(uhid_hint=uhid),
                        "uhid": uhid,
                        "phone": None,
                        "name": None,
                        "age": None,
                        "gender": None,
                    }
                    psn = resolve_clinical_record(rec, anchor_index)
                    if psn:
                        resolved[(rel, f"/visits/{idx}")] = psn
            except Exception:
                continue

    # 3. Lab CSVs and JSONs
    lab_dir = input_dir / "lab"
    if lab_dir.is_dir():
        for p in sorted(lab_dir.glob("*")):
            rel = f"lab/{p.name}"
            if p.suffix == ".csv":
                try:
                    reader = csv.DictReader(p.read_text(encoding="utf-8-sig", errors="replace").splitlines())
                    for idx, row in enumerate(reader):
                        uhid = row.get("uhid")
                        age_sex = row.get("age_sex")
                        age_val, gen_val = None, None
                        if age_sex:
                            m_as = _RE_AGE_SEX.search(age_sex)
                            if m_as:
                                age_val = normalize_age(m_as.group(1))
                                gen_val = normalize_gender(m_as.group(2))
                        rec = {
                            "hospital_id": get_hospital_id(p.name, uhid),
                            "uhid": uhid,
                            "phone": normalize_phone(row.get("phone")),
                            "name": normalize_name(row.get("patient_name")),
                            "age": age_val,
                            "gender": gen_val,
                        }
                        psn = resolve_clinical_record(rec, anchor_index)
                        if psn:
                            resolved[(rel, f"row:{idx}")] = psn
                except Exception:
                    continue
            elif p.suffix == ".json":
                try:
                    data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    pt = data.get("patient") or {}
                    uhid = pt.get("hospital_uhid")
                    phone = normalize_phone(pt.get("phone") or p.name.split("_")[0])
                    rec = {
                        "hospital_id": get_hospital_id(p.name, uhid),
                        "uhid": uhid,
                        "phone": phone,
                        "name": normalize_name(pt.get("name")),
                        "age": normalize_age(pt.get("age")),
                        "gender": normalize_gender(pt.get("sex") or pt.get("gender")),
                    }
                    psn = resolve_clinical_record(rec, anchor_index)
                    if psn:
                        resolved[(rel, "doc")] = psn
                except Exception:
                    continue

    # 4. FHIR Bundles
    fhir_dir = input_dir / "fhir"
    if fhir_dir.is_dir():
        for p in sorted(fhir_dir.glob("*.json")):
            rel = f"fhir/{p.name}"
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                for e in data.get("entry", []):
                    res = (e or {}).get("resource", {})
                    if res.get("resourceType") == "Patient":
                        uhid_val = None
                        for ident in res.get("identifier", []):
                            val = ident.get("value")
                            if val and normalize_uhid(val):
                                uhid_val = val
                                break
                        phone_val = None
                        for tel in res.get("telecom", []):
                            if tel.get("system") == "phone":
                                phone_val = normalize_phone(tel.get("value"))
                                if phone_val:
                                    break
                        rec = {
                            "hospital_id": get_hospital_id(p.name, uhid_val),
                            "uhid": uhid_val,
                            "phone": phone_val,
                            "name": None,
                            "age": None,
                            "gender": normalize_gender(res.get("gender")),
                        }
                        psn = resolve_clinical_record(rec, anchor_index)
                        if psn:
                            resolved[(rel, "doc")] = psn
                        break
            except Exception:
                continue

    return resolved


# =============================================================================
# Wave B: Unstructured Text Files (Discharge, Prescriptions, Lab Txt, Notes)
# =============================================================================

def resolve_wave_b(
    input_dir: Path,
    anchor_index: dict[str, Any],
) -> dict[tuple[str, str], str]:
    """Extract and resolve unstructured clinical text records (loc='doc')."""
    resolved: dict[tuple[str, str], str] = {}

    target_dirs = ("discharge", "prescriptions", "lab", "notes")
    for dname in target_dirs:
        dir_path = input_dir / dname
        if not dir_path.is_dir():
            continue
        for p in sorted(dir_path.glob("*.txt")):
            rel = f"{dname}/{p.name}"
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                m_uhid = _RE_UHID.search(text) or _RE_UHID.search(p.name)
                uhid_val = m_uhid.group(1) if m_uhid else None

                m_phone = _RE_PHONE.search(text) or _RE_PHONE.search(p.name)
                phone_val = normalize_phone(m_phone.group(0)) if m_phone else None

                rec = {
                    "hospital_id": get_hospital_id(rel, uhid_val),
                    "uhid": uhid_val,
                    "phone": phone_val,
                    "name": None,
                    "age": None,
                    "gender": None,
                }
                psn = resolve_clinical_record(rec, anchor_index)
                if psn:
                    resolved[(rel, "doc")] = psn
            except Exception:
                continue

    return resolved


def propagate_clinical_pseudonyms(
    input_dir: Path,
    reg_records: list[dict],
    reg_psn_map: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    """Propagate verified registration pseudonyms across all clinical and unstructured files.

    Returns the unified mapping covering registration + Wave A + Wave B files.
    """
    anchor_index = build_anchor_index(reg_records, reg_psn_map)
    wave_a_map = resolve_wave_a(input_dir, anchor_index)
    wave_b_map = resolve_wave_b(input_dir, anchor_index)

    unified_map = dict(reg_psn_map)
    unified_map.update(wave_a_map)
    unified_map.update(wave_b_map)
    return unified_map
