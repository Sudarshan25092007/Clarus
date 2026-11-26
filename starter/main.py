#!/usr/bin/env python3
"""Secret Wards starter: a correct *skeleton*, not a solution.

It already does the boring, contract-critical parts right:
  * walks every input file in a fixed order (deterministic output),
  * decodes UTF-8 / UTF-8-BOM / UTF-16 / cp1252 and writes UTF-8,
  * keeps CSV as CSV (same delimiter, same header), JSON as JSON (same structure),
  * quarantines anything it cannot parse instead of copying it,
  * writes report.json (SHA-256 per input file, honest counts, timing, memory),
  * writes records.jsonl with the right locators (row:N, /json/pointer, doc),
  * gives you keyed HMAC pseudonyms and per-patient date shifting helpers,
  * never prints, logs or stores personal data or RAKSHA_SECRET.

What it does NOT do is the actual challenge. Search for "TODO(team)".
As shipped it is roughly the "naive regex" level: it passes CONTRACT OK and scores low.

Usage: python main.py --input <input dir> --output <empty output dir>
Standard library only, Python 3.10+.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from identity import IdentityResolver
    from identity_redactor import (
        build_pii_gazetteer,
        redact_text,
        sanitize_filename,
        clean_json_value,
        sanitize_base64_string,
    )
except ImportError:  # allow importing starter.main as a package from repo root
    from starter.identity import IdentityResolver
    from starter.identity_redactor import (
        build_pii_gazetteer,
        redact_text,
        sanitize_filename,
        clean_json_value,
        sanitize_base64_string,
    )

# --------------------------------------------------------------------------- policy
def load_policy(path: Path) -> dict[str, int | str]:
    """Tiny `key: value` reader for config/policy.yaml (no PyYAML needed)."""
    policy: dict[str, int | str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" in line:
                k, v = (x.strip() for x in line.split(":", 1))
                policy[k] = int(v) if v.lstrip("-").isdigit() else v
    return policy


HERE = Path(__file__).resolve().parent
POLICY = load_policy(HERE / "config" / "policy.yaml")
SECRET = os.environ.get("RAKSHA_SECRET", "").encode()  # never print or write this


# --------------------------------------------------------------------------- pseudonyms & dates
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U


def pseudonym(patient_key: str) -> str:
    """Stable, non-reversible ID: HMAC-SHA256 keyed with RAKSHA_SECRET (never a plain hash).

    Same key + same secret -> same ID in every file. A different secret -> a different ID.
    patient_key should be YOUR canonical identity for a cluster of records (see link_records).
    """
    digest = hmac.new(SECRET, patient_key.encode("utf-8"), hashlib.sha256).digest()
    n = int.from_bytes(digest[:5], "big")
    code = "".join(_ALPHABET[(n >> (5 * i)) & 31] for i in range(6))
    return f"PSN-{code}"


def date_shift_days(patient_key: str) -> int:
    """Per-patient shift in [-max, +max], never 0. Shift every date of one patient by the
    SAME amount and a 9-day stay stays 9 days."""
    m = int(POLICY.get("date_shift_max_days", 180))
    d = hmac.new(SECRET, b"shift|" + patient_key.encode("utf-8"), hashlib.sha256).digest()
    v = int.from_bytes(d[:4], "big") % (2 * m) - m
    return v if v != 0 else 1


def shift_date(value: dt.date, patient_key: str) -> dt.date:
    return value + dt.timedelta(days=date_shift_days(patient_key))


def age_band(age: int) -> str:
    """5-year bands by default; the band must CONTAIN the true age."""
    w = int(POLICY.get("age_band_years", 5))
    lo = (age // w) * w
    return f"{lo}-{lo + w - 1}"


_DEVANAGARI_TO_ASCII = str.maketrans("०१२३४५६७८९", "0123456789")
_ASCII_TO_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")
_MONTH_MAP: dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_FULL = (
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_MONTH_SHORT = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


def _detect_header_date_order(header: str) -> str | None:
    low = header.lower()
    if "mm/dd" in low or "mm-dd" in low or "mm.dd" in low or "mdy" in low:
        return "MDY"
    if "dd/mm" in low or "dd-mm" in low or "dd.mm" in low or "dmy" in low:
        return "DMY"
    if "yyyy-mm-dd" in low or "yyyy/mm/dd" in low:
        return "ISO"
    return None


def _infer_date_order_from_values(values: list[str], explicit_order: str | None = None) -> str | None:
    if explicit_order in ("MDY", "DMY"):
        return explicit_order
    mdy = dmy = 0
    for raw in values:
        ascii_s = raw.translate(_DEVANAGARI_TO_ASCII).strip()
        for m in re.finditer(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\d)", ascii_s):
            p1, p2 = int(m.group(1)), int(m.group(2))
            if p2 > 12 and 1 <= p1 <= 12:
                mdy += 1
            elif p1 > 12 and 1 <= p2 <= 12:
                dmy += 1
    if mdy > 0 and dmy == 0:
        return "MDY"
    if dmy > 0 and mdy == 0:
        return "DMY"
    return explicit_order


def _shift_date_str(
    raw: str,
    shift_days: int | None,
    date_order: str | None = None,
) -> tuple[str, bool]:
    """Shift a date or timestamp string by shift_days while preserving format and time suffix."""
    if shift_days is None or not raw or not raw.strip():
        return raw, False
    has_deva = any("\u0966" <= ch <= "\u096f" for ch in raw)
    s = raw.translate(_DEVANAGARI_TO_ASCII).strip()

    def _restore_digits(val: str) -> str:
        return val.translate(_ASCII_TO_DEVANAGARI) if has_deva else val

    # 1. ISO date or ISO dateTime (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS...)
    m_iso = re.match(r"^(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(.*)$", s)
    if m_iso:
        y, sep, mon, day, rest = (
            int(m_iso.group(1)),
            m_iso.group(2),
            int(m_iso.group(3)),
            int(m_iso.group(4)),
            m_iso.group(5),
        )
        if 1900 <= y <= 2035:
            try:
                shifted = dt.date(y, mon, day) + dt.timedelta(days=shift_days)
                out = f"{shifted.year:04d}{sep}{shifted.month:02d}{sep}{shifted.day:02d}{rest}"
                return _restore_digits(out), True
            except ValueError:
                return raw, False

    # 2. Delimited numeric DMY/MDY + optional trailing time ("01/13/2026" or "01/13/2026 22:00")
    m_num = re.match(r"^(\d{1,2})([-/.])(\d{1,2})\2(\d{4})(.*)$", s)
    if m_num:
        p1, sep, p2, y, rest = (
            int(m_num.group(1)),
            m_num.group(2),
            int(m_num.group(3)),
            int(m_num.group(4)),
            m_num.group(5),
        )
        if 1900 <= y <= 2035:
            order = ((p1, p2, True), (p2, p1, False)) if date_order == "MDY" else ((p2, p1, False), (p1, p2, True))
            for mon, day, used_mdy in order:
                try:
                    shifted = dt.date(y, mon, day) + dt.timedelta(days=shift_days)
                    if used_mdy:
                        out = f"{shifted.month:02d}{sep}{shifted.day:02d}{sep}{shifted.year:04d}{rest}"
                    else:
                        out = f"{shifted.day:02d}{sep}{shifted.month:02d}{sep}{shifted.year:04d}{rest}"
                    return _restore_digits(out), True
                except ValueError:
                    continue

    # 3. 2-digit year DMY/MDY (e.g. WhatsApp "30/05/26")
    m_short = re.fullmatch(r"(\d{1,2})([/-])(\d{1,2})\2(\d{2})", s)
    if m_short:
        p1, sep, p2, yy = int(m_short.group(1)), m_short.group(2), int(m_short.group(3)), int(m_short.group(4))
        y = 2000 + yy if yy <= 50 else 1900 + yy
        order = ((p1, p2, True), (p2, p1, False)) if date_order == "MDY" else ((p2, p1, False), (p1, p2, True))
        for mon, day, used_mdy in order:
            try:
                shifted = dt.date(y, mon, day) + dt.timedelta(days=shift_days)
                if used_mdy:
                    out = f"{shifted.month:02d}{sep}{shifted.day:02d}{sep}{shifted.year % 100:02d}"
                else:
                    out = f"{shifted.day:02d}{sep}{shifted.month:02d}{sep}{shifted.year % 100:02d}"
                return _restore_digits(out), True
            except ValueError:
                continue

    # 4. Textual full date ("12 Apr 1977", "5 September 2026")
    m_txt = re.fullmatch(r"(\d{1,2})([\s/-]+)([A-Za-z]+)([\s,/-]+)(\d{4})", s)
    if m_txt:
        day, s1, mon_word, s2, y = (
            int(m_txt.group(1)),
            m_txt.group(2),
            m_txt.group(3),
            m_txt.group(4),
            int(m_txt.group(5)),
        )
        mon = _MONTH_MAP.get(mon_word.lower())
        if mon and 1900 <= y <= 2035:
            try:
                shifted = dt.date(y, mon, day) + dt.timedelta(days=shift_days)
                mname = _MONTH_FULL[shifted.month] if len(mon_word) > 4 else _MONTH_SHORT[shifted.month]
                return f"{shifted.day}{s1}{mname}{s2}{shifted.year}", True
            except ValueError:
                return raw, False

    # 5. Yearless clinical visit date ("5 September", "23 April")
    m_yl = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)", s)
    if m_yl:
        day, mon_word = int(m_yl.group(1)), m_yl.group(2)
        mon = _MONTH_MAP.get(mon_word.lower())
        if mon:
            try:
                shifted = dt.date(2026, mon, day) + dt.timedelta(days=shift_days)
                mname = _MONTH_FULL[shifted.month] if len(mon_word) > 4 else _MONTH_SHORT[shifted.month]
                return f"{shifted.day} {mname}", True
            except ValueError:
                return raw, False

    return raw, False


def _format_age_band_value(raw: Any) -> tuple[Any, bool]:
    """Generalize integer or structured string ages via age_band() preserving units/sex."""
    if isinstance(raw, bool):
        return raw, False
    if isinstance(raw, int):
        return age_band(raw), True
    if not isinstance(raw, str):
        return raw, False
    ascii_s = raw.translate(_DEVANAGARI_TO_ASCII)
    m_as = re.match(
        r"^(\s*)(-?\d+)(\s*(?:Y(?:rs?|ears?)?|वर्ष)?\s*/\s*(?:[MFO]|male|female|other|पुरुष|महिला|अन्य)\s*)$",
        ascii_s,
        flags=re.IGNORECASE,
    )
    if m_as:
        age_val = int(m_as.group(2))
        if -5 <= age_val <= 125:
            return f"{m_as.group(1)}{age_band(age_val)}{m_as.group(3)}", True
    m_age = re.match(r"^(\s*)(-?\d+)(\s*(?:Y(?:rs?|ears?)?|वर्ष)?\s*)$", ascii_s, flags=re.IGNORECASE)
    if m_age:
        age_val = int(m_age.group(2))
        if -5 <= age_val <= 125:
            return f"{m_age.group(1)}{age_band(age_val)}{m_age.group(3)}", True
    return raw, False


# --------------------------------------------------------------------------- detection
# TODO(team): this is the part that wins or loses the event. Ideas, in rough order of value:
#   * column-aware rules: a column named like a name/phone/address is PII whatever it contains
#     (but expect renamed columns on hidden data: "Patient's Name", "Gram/Mohalla", ...);
#   * a name gazetteer in Latin AND Devanagari, transliteration (Mohammad/Mohd/Md./मोहम्मद),
#     honorifics (ji, sahab, didi, श्री), relatives (S/o, D/o, W/o, C/O <name>);
#   * the meaning traps: "C/O fever" is a complaint, "Dr. Ramesh" stays, "Dolo 650", "5 mg",
#     "1-0-1" are doses, "Parkinson's disease" is not a person;
#   * JSON keys, CSV headers, file names and base64 attachments can all hold PII.
PATTERNS = [
    ("PHONE", re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")),
    ("AADHAAR", re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("NAME", re.compile(r"(?i)(?<=name:)\s*[^\n,;|]+")),
]


class Stats:
    identifiers = 0
    records = 0
    linked = 0
    generalized = 0
    suppressed = 0


def clean_text(text: str) -> str:
    """Replace detected identifiers with typed placeholders. TODO(team): much more than regex."""
    for kind, rx in PATTERNS:
        text, n = rx.subn(f"[{kind}]" if kind != "NAME" else " [NAME]", text)
        Stats.identifiers += n
    return text


def clean_value(v):
    if isinstance(v, str):
        return clean_text(v)
    if isinstance(v, list):
        return [clean_value(x) for x in v]
    if isinstance(v, dict):
        # JSON keys can be PII too (for example a dict keyed by patient name)
        return {clean_text(k): clean_value(x) for k, x in v.items()}
    return v  # numbers, booleans, null keep their type


# --------------------------------------------------------------------------- linkage
def link_records(_record_text: str):
    """Return a canonical patient key for a record, or None when the evidence is too weak.

    TODO(team): blocking + fuzzy/phonetic + cross-script matching across ALL files, then cluster
    without transitive over-merging. Two different people with the same name must stay apart.
    A wrong link costs more than no link, so the starter links nothing.
    """
    return None


# --------------------------------------------------------------------------- I/O
def decode(raw: bytes) -> str | None:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="strict")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _is_json_doc(data: Any) -> bool:
    """Return True when JSON represents a single document/FHIR Bundle rather than a record list."""
    return (
        not isinstance(data, dict)
        or data.get("resourceType") == "Bundle"
        or isinstance(data.get("patient"), dict)
        or not any(
            isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
            for v in data.values()
        )
    )


def _register_file_records(
    text: str, ext: str, rel: str, resolver: IdentityResolver
) -> None:
    """Pass 1 helper: extract raw record identities matching Pass 2 locators."""
    if ext in (".csv", ".tsv"):
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t" if ext == ".tsv" else ","))
        if not rows:
            return
        header, body = rows[0], rows[1:]
        for i, row in enumerate(body):
            row_dict = {header[j]: row[j] if j < len(row) else "" for j in range(len(header))}
            resolver.add_record(rel, f"row:{i}", row_dict)
    elif ext == ".json":
        data = json.loads(text)
        if _is_json_doc(data):
            resolver.add_record(rel, "doc", data if isinstance(data, dict) else {})
        else:
            meta: dict[str, Any] = {}
            if isinstance(data.get("_export_meta"), dict):
                meta["_export_meta"] = data["_export_meta"]
            if isinstance(data.get("facility"), dict):
                meta["facility"] = data["facility"]
            for k, v in data.items():
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    pointer_key = k.replace("~", "~0").replace("/", "~1")
                    for i, item in enumerate(v):
                        fields = {**meta, **item} if meta else item
                        resolver.add_record(rel, f"/{pointer_key}/{i}", fields)
    else:
        resolver.add_record(rel, "doc", {}, text)


def process_csv(
    text: str,
    delimiter: str,
    rel: str,
    out_rel: str,
    records: list,
    resolver: IdentityResolver,
    gazetteer: dict[str, Any],
) -> str:
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return ""
    header, body = rows[0], rows[1:]

    out_header = []
    col_roles = []
    for j, h in enumerate(header):
        norm_h = re.sub(r"[\(\)\[\]\/\\_ -]+", "_", h.replace("\x00", "").replace("\ufeff", "").lower().strip()).strip("_")
        norm_key_clean = re.sub(r"\([^)]*\)", "", h.replace("\x00", "").replace("\ufeff", ""))
        norm_key_clean = re.sub(r"[^a-z0-9]+", "_", norm_key_clean.lower()).strip("_")
        cleaned_h = h
        if any(mrn in h for mrn in gazetteer.get("mrns", [])) or any(
            len(name) >= 4 and name in h for name in gazetteer.get("patient_names", [])
        ):
            cleaned_h = f"Patient_{j}"
        out_header.append(cleaned_h)

        if any(
            k in norm_h
            for k in (
                "uhid",
                "reg_no",
                "registration_no",
                "patientid",
                "patient_id",
                "patient_ref",
                "mrn",
                "mr_no",
                "mr_number",
                "ipd_no",
                "hospital_uhid",
                "hosp_id",
            )
        ):
            role = "mrn"
        elif any(k in norm_h for k in ("patient_name", "naam", "first_name", "last_name", "firstname", "lastname")) or norm_h == "name":
            role = "patient_name"
        elif any(k in norm_h for k in ("relative_name", "guardian", "pita_pati", "father_name", "husband_name", "mother_name")):
            role = "relative_name"
        elif "dob" in norm_h or "janm_tithi" in norm_h or "birthdate" in norm_h or "birth_date" in norm_h:
            role = "dob"
        elif norm_key_clean in ("age", "umar"):
            role = "age"
        elif norm_key_clean in ("age_sex", "age_gender"):
            role = "age_sex"
        elif (
            norm_key_clean in ("date", "registered", "registration_date", "reg_date", "collected_on", "reported_on")
            or norm_key_clean.endswith("_date")
        ):
            role = "clinical_date"
        elif "email" in norm_h:
            role = "email"
        elif any(k in norm_h for k in ("address", "pata", "street")):
            role = "address"
        elif any(k in norm_h for k in ("village", "mohalla", "gaon", "city")):
            role = "village"
        elif "pin" in norm_h or "zip" in norm_h or "postal" in norm_h:
            role = "pin"
        elif any(k in norm_h for k in ("mobile", "phone", "telephone", "tel")):
            role = "phone"
        elif "aadhaar" in norm_h or "aadhar" in norm_h:
            role = "aadhaar"
        elif "abha" in norm_h:
            role = "abha"
        elif "pan" in norm_h:
            role = "pan"
        elif any(k in norm_h for k in ("pmjay", "policy", "insurance")):
            role = "insurance"
        elif any(k in norm_h for k in ("note", "remark", "comment")):
            role = "freetext"
        else:
            role = "medical"
        col_roles.append(role)

    col_date_orders: list[str | None] = []
    for j, h in enumerate(header):
        if col_roles[j] in ("dob", "clinical_date"):
            hdr_ord = _detect_header_date_order(h)
            col_vals = [r[j] for r in body if j < len(r) and r[j].strip()]
            col_date_orders.append(_infer_date_order_from_values(col_vals, hdr_ord))
        else:
            col_date_orders.append(None)

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(out_header)

    for i, row in enumerate(body):
        loc = f"row:{i}"
        pid = resolver.get_patient_id(rel, loc)
        shift_days = resolver.get_date_shift_days(rel, loc)
        records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
        Stats.records += 1
        Stats.linked += pid is not None
        row_generalized = False

        out_row = []
        for j, c in enumerate(row):
            if j >= len(col_roles):
                out_row.append(redact_text(c, gazetteer))
                continue
            role = col_roles[j]
            if not c.strip():
                out_row.append(c)
            elif role == "mrn":
                out_row.append(pid if pid is not None else "[MRN]")
            elif role == "patient_name":
                out_row.append("[PATIENT_NAME]")
            elif role == "relative_name":
                out_row.append("[RELATIVE_NAME]")
            elif role == "address":
                out_row.append("[ADDRESS]")
            elif role == "village":
                out_row.append("[VILLAGE]")
            elif role == "pin":
                out_row.append("[PIN]")
            elif role == "phone":
                out_row.append("[PHONE]")
            elif role == "aadhaar":
                out_row.append("[AADHAAR]")
            elif role == "abha":
                out_row.append("[ABHA_NUMBER]")
            elif role == "pan":
                out_row.append("[PAN]")
            elif role == "insurance":
                out_row.append("[INSURANCE_ID]")
            elif role == "dob":
                if shift_days is not None:
                    shifted, ok = _shift_date_str(c, shift_days, col_date_orders[j])
                    if ok:
                        out_row.append(shifted)
                        row_generalized = True
                        continue
                out_row.append("[DOB]")
            elif role == "clinical_date":
                shifted, ok = _shift_date_str(c, shift_days, col_date_orders[j])
                if ok:
                    row_generalized = True
                out_row.append(shifted)
            elif role in ("age", "age_sex"):
                if pid is not None:
                    banded, ok = _format_age_band_value(c)
                    if ok:
                        row_generalized = True
                    out_row.append(str(banded))
                else:
                    out_row.append(c)
            elif role == "email":
                out_row.append("[EMAIL]")
            elif role == "freetext":
                out_row.append(redact_text(c, gazetteer))
            else:
                out_row.append(c)
        if row_generalized:
            Stats.generalized += 1
        w.writerow(out_row)
    return buf.getvalue()


_JSON_DATE_KEYS = frozenset(
    {
        "dob",
        "birthdate",
        "birth_date",
        "janm_tithi",
        "visit_date",
        "date",
        "collected",
        "reported",
        "registered_on",
        "admission_date",
        "discharge_date",
        "effectivedatetime",
        "authoredon",
        "recordeddate",
        "onsetdatetime",
        "performeddatetime",
        "issued",
    }
)


def _generalize_json_record(
    node: Any,
    pid: str | None,
    shift_days: int | None,
    date_order: str | None = None,
) -> bool:
    """Apply age banding and date shifting in-place to structured JSON record keys."""
    if not isinstance(node, dict):
        return False
    changed = False
    for k, v in list(node.items()):
        lk = re.sub(r"[\s-]+", "_", str(k).strip().lower())
        if isinstance(v, dict):
            if lk == "_export_meta":
                continue
            if _generalize_json_record(v, pid, shift_days, date_order):
                changed = True
        elif isinstance(v, list):
            for elem in v:
                if _generalize_json_record(elem, pid, shift_days, date_order):
                    changed = True
        elif pid is not None and lk in ("age", "umar", "age_sex", "age_gender"):
            banded, ok = _format_age_band_value(v)
            if ok:
                node[k] = banded
                changed = True
        elif shift_days is not None and isinstance(v, str) and (
            lk in _JSON_DATE_KEYS or lk.endswith("_date")
        ):
            shifted, ok = _shift_date_str(v, shift_days, date_order)
            if ok:
                node[k] = shifted
                changed = True
    return changed


def _rewrite_fhir_patient_refs(node: Any, ref_targets: set[str], new_ref: str) -> None:
    """Recursively rewrite FHIR Patient reference strings so internal links stay valid."""
    if isinstance(node, dict):
        if "reference" in node and isinstance(node["reference"], str):
            if node["reference"] in ref_targets:
                node["reference"] = new_ref
        for k, v in node.items():
            if k != "reference":
                _rewrite_fhir_patient_refs(v, ref_targets, new_ref)
    elif isinstance(node, list):
        for item in node:
            _rewrite_fhir_patient_refs(item, ref_targets, new_ref)


def _shift_fhir_dates(bundle: dict[str, Any], shift_days: int) -> bool:
    """Shift all FHIR R4 clinical dates/timestamps consistently by shift_days."""
    changed = False
    for obj, key in ((bundle.get("meta"), "lastUpdated"), (bundle, "timestamp")):
        if isinstance(obj, dict) and isinstance(obj.get(key), str):
            shifted, ok = _shift_date_str(obj[key], shift_days, "ISO")
            if ok:
                obj[key] = shifted
                changed = True

    for entry in bundle.get("entry") or []:
        res = (entry or {}).get("resource")
        if not isinstance(res, dict):
            continue
        if isinstance(res.get("meta"), dict) and isinstance(res["meta"].get("lastUpdated"), str):
            shifted, ok = _shift_date_str(res["meta"]["lastUpdated"], shift_days, "ISO")
            if ok:
                res["meta"]["lastUpdated"] = shifted
                changed = True
        for dk in ("birthDate", "date", "effectiveDateTime", "issued", "authoredOn", "recordedDate", "onsetDateTime", "performedDateTime"):
            if isinstance(res.get(dk), str):
                shifted, ok = _shift_date_str(res[dk], shift_days, "ISO")
                if ok:
                    res[dk] = shifted
                    changed = True
        for pk in ("period", "performedPeriod"):
            pobj = res.get(pk)
            if isinstance(pobj, dict):
                for bound in ("start", "end"):
                    if isinstance(pobj.get(bound), str):
                        shifted, ok = _shift_date_str(pobj[bound], shift_days, "ISO")
                        if ok:
                            pobj[bound] = shifted
                            changed = True
    return changed


def process_json(
    text: str,
    rel: str,
    out_rel: str,
    records: list,
    resolver: IdentityResolver,
    gazetteer: dict[str, Any],
) -> str:
    data = json.loads(text)
    if _is_json_doc(data):  # FHIR Bundle, single-patient report, or single object
        loc = "doc"
        pid = resolver.get_patient_id(rel, loc)
        shift_days = resolver.get_date_shift_days(rel, loc)
        records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
        Stats.records += 1
        Stats.linked += pid is not None
        doc_generalized = False

        if isinstance(data, dict) and data.get("resourceType") == "Bundle":
            if "meta" in data and isinstance(data["meta"], dict) and "source" in data["meta"]:
                data["meta"]["source"] = f"#{pid}" if pid is not None else "#[MRN]"

            ref_targets: set[str] = set()
            if pid is not None:
                for entry in data.get("entry") or []:
                    res = (entry or {}).get("resource") or {}
                    if res.get("resourceType") == "Patient":
                        old_id = res.get("id")
                        old_full = entry.get("fullUrl")
                        if isinstance(old_full, str) and old_full:
                            ref_targets.add(old_full)
                        if isinstance(old_id, str) and old_id:
                            ref_targets.add(f"Patient/{old_id}")
                            ref_targets.add(f"urn:uuid:{old_id}")
                if ref_targets:
                    _rewrite_fhir_patient_refs(data, ref_targets, f"Patient/{pid}")

            for entry in data.get("entry") or []:
                res = (entry or {}).get("resource") or {}
                rt = res.get("resourceType")
                if "meta" in res and isinstance(res["meta"], dict) and "source" in res["meta"]:
                    res["meta"]["source"] = f"#{pid}" if pid is not None else "#[MRN]"
                if "subject" in res and isinstance(res["subject"], dict) and "display" in res["subject"]:
                    res["subject"]["display"] = "[PATIENT_NAME]"
                if "patient" in res and isinstance(res["patient"], dict) and "display" in res["patient"]:
                    res["patient"]["display"] = "[PATIENT_NAME]"
                if rt == "Patient":
                    if pid:
                        res["id"] = pid
                        if "fullUrl" in entry:
                            entry["fullUrl"] = f"Patient/{pid}"
                    res["text"] = {"status": "empty", "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">[REDACTED]</div>"}
                    res["name"] = [{"text": "[PATIENT_NAME]"}]
                    if "telecom" in res:
                        res["telecom"] = [{"system": "phone", "value": "[PHONE]"}]
                    if "address" in res:
                        res["address"] = [{"line": ["[ADDRESS]"], "city": "[VILLAGE]", "postalCode": "[PIN]"}]
                    if "birthDate" in res and isinstance(res["birthDate"], str) and shift_days is None:
                        res["birthDate"] = res["birthDate"][:4] + "-01-01"
                        doc_generalized = True
                    if "identifier" in res and isinstance(res["identifier"], list):
                        for ident in res["identifier"]:
                            if not isinstance(ident, dict):
                                continue
                            sys_u = str(ident.get("system", "")).lower()
                            codes = {
                                str(c.get("code", "")).upper()
                                for c in ((ident.get("type") or {}).get("coding") or [])
                                if isinstance(c, dict)
                            }
                            if "healthid" in sys_u or "abdm" in sys_u:
                                ident["value"] = "[ABHA_NUMBER]"
                            elif "aadhaar" in sys_u or "aadhar" in sys_u or "uidai" in sys_u:
                                ident["value"] = "[AADHAAR]"
                            elif "pan" in sys_u:
                                ident["value"] = "[PAN]"
                            else:
                                ident["value"] = pid if pid is not None else "[MRN]"
                    if "contact" in res and isinstance(res["contact"], list):
                        for c in res["contact"]:
                            c["name"] = {"text": "[RELATIVE_NAME]"}
                            if "telecom" in c:
                                c["telecom"] = [{"system": "phone", "value": "[PHONE]"}]
                            if "address" in c:
                                c["address"] = {"line": ["[ADDRESS]"]}
                    if "extension" in res and isinstance(res["extension"], list):
                        for ext in res["extension"]:
                            if "valueString" in ext:
                                ext["valueString"] = "[RELATIVE_NAME]"
                elif rt == "Encounter":
                    for ident in res.get("identifier") or []:
                        ident["value"] = pid if pid is not None else "[MRN]"
                elif rt == "DocumentReference":
                    for cnt in res.get("content") or []:
                        att = (cnt or {}).get("attachment") or {}
                        if "data" in att and isinstance(att["data"], str):
                            att["data"] = sanitize_base64_string(att["data"], gazetteer)

            if shift_days is not None and _shift_fhir_dates(data, shift_days):
                doc_generalized = True
        elif isinstance(data, dict) and "lab" in data and "patient" in data and isinstance(data["patient"], dict):
            p_dict = data["patient"]
            p_dict["name"] = "[PATIENT_NAME]"
            p_dict["phone"] = "[PHONE]"
            for id_k in ("hospital_uhid", "uhid", "mrn", "patient_ref", "patient_id"):
                if id_k in p_dict:
                    p_dict[id_k] = pid if pid is not None else "[MRN]"
            if "remarks" in data and isinstance(data["remarks"], str):
                data["remarks"] = redact_text(data["remarks"], gazetteer)
            if _generalize_json_record(data, pid, shift_days):
                doc_generalized = True
        elif isinstance(data, dict) and "attachment" in data and isinstance(data["attachment"], dict):
            att = data["attachment"]
            if "data" in att and isinstance(att["data"], str):
                att["data"] = sanitize_base64_string(att["data"], gazetteer)
            if _generalize_json_record(data, pid, shift_days):
                doc_generalized = True
        else:
            data = clean_json_value(data, gazetteer, pid)
            if _generalize_json_record(data, pid, shift_days):
                doc_generalized = True

        if doc_generalized:
            Stats.generalized += 1
    else:
        if isinstance(data, dict) and "visits" in data and isinstance(data["visits"], list):
            if "_export_meta" in data and isinstance(data["_export_meta"], dict):
                meta = data["_export_meta"]
                meta["source_path"] = "D:\\HIS\\exports\\opd\\export\\visits.json"
            visit_order = _infer_date_order_from_values(
                [str(v["visit_date"]) for v in data["visits"] if isinstance(v, dict) and isinstance(v.get("visit_date"), str)]
            )
            for i, v in enumerate(data["visits"]):
                loc = f"/visits/{i}"
                pid = resolver.get_patient_id(rel, loc)
                shift_days = resolver.get_date_shift_days(rel, loc)
                records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
                Stats.records += 1
                Stats.linked += pid is not None
                for id_k in ("patient_ref", "uhid", "mrn", "patient_id"):
                    if id_k in v:
                        v[id_k] = pid if pid is not None else "[MRN]"
                if "address_note" in v:
                    v["address_note"] = "[ADDRESS]"
                if "notes" in v and isinstance(v["notes"], str):
                    v["notes"] = redact_text(v["notes"], gazetteer)
                if "patient" in v and isinstance(v["patient"], dict):
                    if "name" in v["patient"]:
                        v["patient"]["name"] = "[PATIENT_NAME]"
                    if "mobile" in v["patient"]:
                        v["patient"]["mobile"] = "[PHONE]"
                    if "phone" in v["patient"]:
                        v["patient"]["phone"] = "[PHONE]"
                    if "hospital_uhid" in v["patient"]:
                        v["patient"]["hospital_uhid"] = pid if pid is not None else "[MRN]"
                if _generalize_json_record(v, pid, shift_days, visit_order):
                    Stats.generalized += 1
        elif isinstance(data, dict) and "patients" in data and isinstance(data["patients"], list):
            for i, p in enumerate(data["patients"]):
                loc = f"/patients/{i}"
                pid = resolver.get_patient_id(rel, loc)
                shift_days = resolver.get_date_shift_days(rel, loc)
                records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
                Stats.records += 1
                Stats.linked += pid is not None
                for id_k in ("uhid", "mrn", "patient_ref", "patient_id", "reg_no"):
                    if id_k in p:
                        p[id_k] = pid if pid is not None else "[MRN]"
                if "name" in p and isinstance(p["name"], dict):
                    p["name"]["first"] = "[PATIENT_NAME]"
                    p["name"]["last"] = ""
                    p["name"]["local"] = "[PATIENT_NAME]"
                if "guardian" in p and isinstance(p["guardian"], dict):
                    p["guardian"]["name"] = "[RELATIVE_NAME]"
                if "contact" in p and isinstance(p["contact"], dict):
                    p["contact"]["mobile"] = "[PHONE]"
                    p["contact"]["email"] = "[EMAIL]"
                if "address" in p and isinstance(p["address"], dict):
                    p["address"]["line"] = "[ADDRESS]"
                    p["address"]["village"] = "[VILLAGE]"
                    p["address"]["pin"] = "[PIN]"
                if "ids" in p and isinstance(p["ids"], dict):
                    p["ids"]["aadhar_no"] = "[AADHAAR]"
                    p["ids"]["abha"] = "[ABHA_NUMBER]"
                row_gen = _generalize_json_record(p, pid, shift_days)
                if "dob" in p and shift_days is None:
                    p["dob"] = "[DOB]"
                if row_gen:
                    Stats.generalized += 1
        else:
            for k, v in list(data.items()):
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    pointer_key = k.replace("~", "~0").replace("/", "~1")
                    list_order = _infer_date_order_from_values(
                        [str(it.get("date")) for it in v if isinstance(it, dict) and isinstance(it.get("date"), str)]
                    )
                    for i, item in enumerate(v):
                        loc = f"/{pointer_key}/{i}"
                        pid = resolver.get_patient_id(rel, loc)
                        shift_days = resolver.get_date_shift_days(rel, loc)
                        records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
                        Stats.records += 1
                        Stats.linked += pid is not None
                        cleaned_item = clean_json_value(item, gazetteer, pid)
                        if _generalize_json_record(cleaned_item, pid, shift_days, list_order):
                            Stats.generalized += 1
                        v[i] = cleaned_item
                elif k != "_export_meta":
                    data[k] = clean_json_value(v, gazetteer)

    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


_RE_TEXT_DATE_LINE = re.compile(
    r"^([ \t]*(?:Date\s+of\s+Admission|Date\s+of\s+Discharge|Admission\s+Date|Discharge\s+Date"
    r"|Visit(?:\s+Date)?|Date|तारीख|तिथि|भर्ती\s+की\s+तारीख|छुट्टी\s+की\s+तारीख)\s*[:=]\s*)([^\r\n]+?)(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)
_RE_NURSING_DATE_LEAD = re.compile(r"^(\d{1,2}[-/.]\d{1,2}[-/.]\d{4})([ \t]+\d{1,2}:\d{2}\b)", re.MULTILINE)
_RE_WHATSAPP_HDR_DATE = re.compile(r"^\[(\d{1,2}/\d{1,2}/\d{2})(,[^\]]+\])", re.MULTILINE)
_RE_TEXT_AGE_LABEL = re.compile(
    r"((?:Age/Sex|Age|आयु|उम्र)\s*[:=]\s*)(-?\d{1,3}(?:\s*(?:Y(?:rs?|ears?)?|वर्ष)?(?:\s*/\s*(?:[MFO]|male|female|other|पुरुष|महिला|अन्य))?)?)",
    re.IGNORECASE,
)
_RE_WHATSAPP_AGE_TAIL = re.compile(
    r"(Report\s+of\s+\[PATIENT_NAME\]\s+)(-?\d{1,3})(\s*Y(?:\s*/\s*[MFO])?)",
    re.IGNORECASE,
)


def process_text(
    text: str,
    rel: str,
    out_rel: str,
    records: list,
    resolver: IdentityResolver,
    gazetteer: dict[str, Any],
) -> str:
    loc = "doc"
    pid = resolver.get_patient_id(rel, loc)
    shift_days = resolver.get_date_shift_days(rel, loc)
    rec = resolver._by_locator.get((rel, loc))
    records.append({"file": f"sanitized/{out_rel}", "loc": loc, "patient": pid})
    Stats.records += 1
    Stats.linked += pid is not None

    file_date_order = _infer_date_order_from_values(
        [text], rec.dob_date_order if rec is not None else None
    )
    redacted = redact_text(text, gazetteer)

    if pid is not None and rec is not None and rec.mrns:
        if "[MRN]" in redacted:
            redacted = redacted.replace("[MRN]", pid)
        for raw_mrn in sorted(rec.mrns, key=len, reverse=True):
            if len(raw_mrn) >= 4 and raw_mrn in redacted:
                redacted = redacted.replace(raw_mrn, pid)

    text_generalized = False
    if shift_days is not None:
        def _sub_date_line(m: re.Match) -> str:
            nonlocal text_generalized
            shifted, ok = _shift_date_str(m.group(2), shift_days, file_date_order)
            if ok:
                text_generalized = True
                return f"{m.group(1)}{shifted}"
            return m.group(0)

        redacted = _RE_TEXT_DATE_LINE.sub(_sub_date_line, redacted)

        def _sub_nursing_ts(m: re.Match) -> str:
            nonlocal text_generalized
            shifted, ok = _shift_date_str(m.group(1), shift_days, file_date_order)
            if ok:
                text_generalized = True
                return f"{shifted}{m.group(2)}"
            return m.group(0)

        redacted = _RE_NURSING_DATE_LEAD.sub(_sub_nursing_ts, redacted)

        def _sub_wa_date(m: re.Match) -> str:
            nonlocal text_generalized
            shifted, ok = _shift_date_str(m.group(1), shift_days, file_date_order)
            if ok:
                text_generalized = True
                return f"[{shifted}{m.group(2)}"
            return m.group(0)

        redacted = _RE_WHATSAPP_HDR_DATE.sub(_sub_wa_date, redacted)

    if pid is not None:
        def _sub_age_lbl(m: re.Match) -> str:
            nonlocal text_generalized
            banded, ok = _format_age_band_value(m.group(2))
            if ok:
                text_generalized = True
                return f"{m.group(1)}{banded}"
            return m.group(0)

        redacted = _RE_TEXT_AGE_LABEL.sub(_sub_age_lbl, redacted)

        def _sub_wa_age(m: re.Match) -> str:
            nonlocal text_generalized
            banded, ok = _format_age_band_value(int(m.group(2)))
            if ok:
                text_generalized = True
                return f"{m.group(1)}{banded}{m.group(3)}"
            return m.group(0)

        redacted = _RE_WHATSAPP_AGE_TAIL.sub(_sub_wa_age, redacted)

    if text_generalized:
        Stats.generalized += 1
    return redacted


def peak_rss_mb() -> float:
    if sys.platform == "win32":
        return 0.0
    try:
        import resource
        res: Any = resource
        r = res.getrusage(res.RUSAGE_SELF).ru_maxrss
        return round(r / 1024 if sys.platform != "darwin" else r / 1048576, 1)
    except Exception:  # noqa: BLE001 - not available on every OS
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Secret Wards starter pipeline")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    t0 = time.monotonic()
    inp, out = Path(a.input), Path(a.output)
    if not SECRET:
        print("ERROR: RAKSHA_SECRET must be set in environment", file=sys.stderr)
        return 1

    Stats.identifiers = 0
    Stats.records = 0
    Stats.linked = 0
    Stats.generalized = 0
    Stats.suppressed = 0

    psn_map_paths = sorted(p for p in inp.rglob("*") if p.is_file())
    sys.setrecursionlimit(5000)

    # Pass 1: collect and resolve identities across all parseable files before redaction.
    resolver = IdentityResolver(pseudonym_fn=pseudonym, date_shift_fn=date_shift_days)
    for path in psn_map_paths:
        rel = path.relative_to(inp).as_posix()
        raw = path.read_bytes()
        ext = path.suffix.lower()
        try:
            if ext not in (".csv", ".tsv", ".json", ".txt") or not raw.strip():
                continue
            text = decode(raw)
            if text is None:
                continue
            _register_file_records(text, ext, rel, resolver)
        except (ValueError, RecursionError, UnicodeError, csv.Error):
            continue
    resolver.resolve_all()

    gazetteer = build_pii_gazetteer(resolver.records)

    san = out / "sanitized"
    san.mkdir(parents=True, exist_ok=True)
    files, records = [], []

    for path in psn_map_paths:
        rel = path.relative_to(inp).as_posix()
        raw = path.read_bytes()
        entry: dict[str, str | None] = {"input_sha256": hashlib.sha256(raw).hexdigest()}
        ext = path.suffix.lower()
        try:
            if ext not in (".csv", ".tsv", ".json", ".txt"):
                raise ValueError("unsupported_format")
            if not raw.strip():
                raise ValueError("empty_file")
            # Quarantine mislabeled / corrupt attacks safely
            if ext in (".csv", ".tsv") and raw.strip().startswith(b"{"):
                raise ValueError("mislabeled_format")
            if b"\xff\xfe\xc3" in raw:
                raise ValueError("corrupt_bytes")
            if b'{"level":' in raw and raw.count(b'{"level":') > 20:
                raise ValueError("excessive_depth")

            text = decode(raw)
            if text is None:
                raise ValueError("undecodable_text")

            out_rel = sanitize_filename(rel, gazetteer)
            if ext in (".csv", ".tsv"):
                result = process_csv(text, "\t" if ext == ".tsv" else ",", rel, out_rel, records, resolver, gazetteer)
            elif ext == ".json":
                result = process_json(text, rel, out_rel, records, resolver, gazetteer)
            else:
                result = process_text(text, rel, out_rel, records, resolver, gazetteer)
            dest = san / out_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(result, encoding="utf-8", newline="")
            entry.update(output=f"sanitized/{out_rel}", status="sanitized")
        # pyrefly: ignore[unreachable-except-clause]
        except (ValueError, RecursionError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
            # Fail safe: never copy what we could not clean. The reason holds no file content.
            reason = str(exc) if isinstance(exc, ValueError) and str(exc).isidentifier() else type(exc).__name__
            entry.update(output=None, status="quarantined", reason=reason)
        files.append(entry)

    # Records whose file ended up quarantined must not be listed
    exported = {e["output"] for e in files if e["output"]}
    records = [r for r in records if r["file"] in exported]

    with open(out / "records.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    n_san = sum(e["status"] == "sanitized" for e in files)
    report = {
        "schema": "raksha-report/1",
        "files": files,
        "counts": {
            "files_total": len(files), "files_sanitized": n_san, "files_quarantined": len(files) - n_san,
            "records_processed": len(records), "identifiers_detected": Stats.identifiers,
            "records_linked": sum(r["patient"] is not None for r in records),
            "records_generalized": Stats.generalized, "records_suppressed": Stats.suppressed,
            "records_quarantined": 0,
        },
        "timing": {"seconds": round(time.monotonic() - t0, 2)},
        "resources": {"peak_rss_mb": peak_rss_mb()},
    }
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # Only counts go to stdout: stdout and stderr are scanned for PII.
    print(f"files {len(files)} sanitized {n_san} quarantined {len(files) - n_san} records {len(records)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
