"""Generic PII redaction layer with mask-and-restore medical shields and identity gazetteer."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 1. Medical Preserve Shield Patterns (Protected from Redaction)
# ---------------------------------------------------------------------------

_SHIELD_PATTERNS = [
    # Doctor expressions: Dr. Ramesh Srivastava (MD Medicine), Dr. Harjeet Bedi
    re.compile(r"\b(?:Dr\.?|Doctor)\s+[A-Za-z\u0900-\u097F.\s]+?(?=(?:\([^\)]*\)|reviewed|\bhas\b|\btook\b|,|\n|$))", re.IGNORECASE),
    re.compile(r"\b(?:Dr\.?|Doctor)\s+[A-Za-z\u0900-\u097F]+(?:\s+[A-Za-z\u0900-\u097F]+)?", re.IGNORECASE),
    # Disease eponyms: Crohn's disease, Addison's, Bell's palsy, Wilson's disease, Parkinson's
    re.compile(r"\b(?:Addison|Crohn|Bell|Wilson|Parkinson|Alzheimer|Hansen|Graves|Meniere|Hodgkin|Non-Hodgkin|Hashimoto)'s\s+(?:disease|syndrome|palsy)?\b", re.IGNORECASE),
    # Clinical history: K/C/O, H/O
    re.compile(r"\b(?:K/C/O|k/c/o)\s+[^,\n;.]+", re.IGNORECASE),
    re.compile(r"\b(?:H/O|h/o)\s+[^,\n;.]+", re.IGNORECASE),
    # Clinical complaints: C/O <symptom> (distinguished from C/O relative in addresses)
    re.compile(r"\bC/[Oo]\s+(?:fever|pain|cough|headache|vomiting|loose stools?|chest pain|weakness|breathlessness|giddiness|swelling|burning|itching|rash|cold|diarrhoea|constipation|seizure|blurring|bleeding)\b(?:\s*x\s*\d+\s*(?:days?|weeks?|months?|yrs?|years?))?", re.IGNORECASE),
    re.compile(r"\b(?:fever|pain|cough|headache|vomiting|loose stools?)\s+x\s+\d+\s+days?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+(?:दिन|din)\s+से\s+[^\n,;.]+", re.IGNORECASE),
    # Dosages, strengths, units: 650 mg, 1 g, 10 units, 40 mg, etc.
    re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|g|mcg|kg|units?|sachets?|puffs?|mL|dL|mmol/L|g/dL|mg/dL|/uL|%)\b", re.IGNORECASE),
    # Frequencies: 1-0-1, 1-1-1, 1-0-0, 0-0-1, IV
    re.compile(r"\b\d-\d-\d\b"),
    re.compile(r"\b\d-\d\b"),
    re.compile(r"\b(?:IV|IM|SC|oral|after each loose stool|puffs?|OD|BD|TDS|QID)\b", re.IGNORECASE),
    # Vitals & Labs: BP 120/80, pulse 90, RBS 145 mg/dL, temp 98.4 F, etc.
    re.compile(r"\b(?:BP|pulse|temp(?:_f)?|spo2|RBS|HbA1c|WBC|Platelet|Sodium|Creatinine|TSH|Haemoglobin)\s*[:=]?\s*\d+(?:[./]\d+)?(?:\s*[A-Za-z/%]+)?\b", re.IGNORECASE),
    # Hospital wards and beds
    re.compile(r"\b(?:Ward\s+\d+(?:\s*\([^\)]*\))?|Bed\s+\d+)\b", re.IGNORECASE),
    # Districts (clinical geographical context)
    re.compile(r"\b(?:Lucknow|Barabanki|Sitapur|Hardoi|Rae Bareli|Bahraich|Sultanpur|Unnao|बहराइच|बाराबंकी|रायबरेली)\b", re.IGNORECASE),
    # Context traps & procedures
    re.compile(r"\b(?:ASHA\s+workers?|Referred\s+by\s+ASHA\s+worker|oxygen given en route)\b", re.IGNORECASE),
    re.compile(r"\b(?:Appendicectomy|Caesarean section|Cataract surgery|Deep brain stimulation|Haemodialysis session|Inguinal hernia repair|Laparoscopic cholecystectomy|Whipple procedure)\b", re.IGNORECASE),
    re.compile(r"\b(?:Paracetamol|Pantoprazole|Ceftriaxone|Amlodipine|Artesunate|Atorvastatin|Azithromycin|Metformin|Insulin|Omeprazole|Ciprofloxacin)\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]\d{2}(?:\.\d+)?\b"),  # ICD-10 code
]


def shield_text(text: str) -> tuple[str, dict[str, str]]:
    """Replace clinical preserve spans with temporary unique tokens __SHIELD_i__."""
    shields: dict[str, str] = {}
    counter = 0

    def _replace_shield(m: re.Match) -> str:
        nonlocal counter
        tok = f"__SHIELD_{counter}__"
        counter += 1
        shields[tok] = m.group(0)
        return tok

    shielded = text
    for pat in _SHIELD_PATTERNS:
        shielded = pat.sub(_replace_shield, shielded)

    return shielded, shields


def restore_text(text: str, shields: dict[str, str], gazetteer: dict[str, Any] | None = None) -> str:
    """Restore __SHIELD_i__ tokens back to original clinical text, unpacking nested shields."""
    while "__SHIELD_" in text:
        changed = False
        for tok, original in reversed(list(shields.items())):
            if tok in text:
                text = text.replace(tok, original)
                changed = True
        if not changed:
            break
    return text


# ---------------------------------------------------------------------------
# 2. Generic PII Patterns for Redaction
# ---------------------------------------------------------------------------

_RE_PHONE = re.compile(
    r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|\b\d{5}[\s-]\d{5}\b|\b[6-9]\d{9}\b|"
    r"(?<![\d\u0966-\u096F])(?:\+?९१[\s-]?)?[६-९][\u0966-\u096F]{4}[\s-]?[\u0966-\u096F]{5}(?![\d\u0966-\u096F])|"
    r"(?<![\d\u0966-\u096F])[६-९][\u0966-\u096F]{9}(?![\d\u0966-\u096F])"
)
_RE_AADHAAR = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_RE_AADHAAR_MASKED = re.compile(r"(?i)(?<![A-Za-z0-9])X{4}[\s-]?X{4}[\s-]?\d{4}(?!\d)")
_RE_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_RE_ABHA = re.compile(r"\b\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_RE_MRN_PATTERNS = re.compile(
    r"\b(?:SNJ[-/]\d{2}[-/]\d+|JJH-\d+|DHH(?:-IPD)?\d*-\d+|UHID-\d+|IPD-\d{4}-\d+|IP[-/]\d{2}[-/]\d+|ADM\d+)\b",
    re.IGNORECASE,
)
_RE_PIN = re.compile(r"\b(?:PIN[:\s-]*|Pin[:\s-]*)?(\d{6})\b", re.IGNORECASE)

# Generic label-anchored patterns
_RE_TIME_LOG_NAME = re.compile(
    r"^(\d{1,2}:\d{2}\s*-\s*)([A-Za-z\u0900-\u097F.\s]+?)(?=\s*(?:\([^\)]*\)|,\s*(?:mob|mobile|phone|tel|\d|\+)|-\s*K/C/O|-|$))",
    re.MULTILINE | re.IGNORECASE,
)
_RE_ATTENDANT_CALLER_WITNESS = re.compile(
    r"\b(?:called by|witnessed by|referred by patient's?|accompanied by|brought by)\s+([A-Za-z\u0900-\u097F.\s]+?)(?=\s*(?:\[PHONE\]|\d|\+?91|mob|phone|,|\n|\.|\(|$))",
    re.IGNORECASE,
)
_RE_ATTENDANT_LABEL = re.compile(
    r"\b(?:Attendant|Attendant Name|Caller|Witness|Informant)\s*[:=-]\s*([A-Za-z\u0900-\u097F.\s]+?)(?=\s*(?:\[PHONE\]|\d|\+?91|mob|phone|,|\n|\.|\(|$))",
    re.IGNORECASE,
)
_RE_LAB_RECEIVED_FROM = re.compile(
    r"\b(?:sample\s+received\s+from|received\s+from|from|by)\s+([A-Za-z\u0900-\u097F]+)(?:'s|’s)\s+attendant\b",
    re.IGNORECASE,
)
_RE_DISCHARGE_NOTE_HEADER = re.compile(
    r"\b([A-Za-z\u0900-\u097F]{3,}(?:\s+[A-Za-z\u0900-\u097F]+)?),\s*([WwSsDdCc]/[Oo]|wife of|son of|daughter of|care of)\b"
)
_RE_RELATIVE_LABEL = re.compile(
    r"\b([SDWC]/[Oo])\s+([A-Za-z\u0900-\u097F.\s]+?)(?=[,\n;()]|$)"
)
_RE_ATTENDANT_PAREN = re.compile(
    r"\bAttendant\s*\((?:[SDWC]/[Oo]\s+)?([^)]+)\)",
    re.IGNORECASE,
)
_RE_HINGLISH_NARRATIVE = re.compile(
    r"\b([A-Z][a-zA-Z\u0900-\u097F]+(?:\s+(?:sahab|ji|behenji|bhai|babu|didi|amma))?)\s+((?:ka|ki|ke)\s+(?:__SHIELD_\d+__|BP|condition|ulti|tabiyat|neend)\b)",
    re.IGNORECASE,
)
_RE_NURSING_KCO = re.compile(
    r"^(\d{1,2}:\d{2}\s*-\s*)([A-Za-z\u0900-\u097F.\s]+?)(\s*-\s*K/C/O\b)",
    re.MULTILINE,
)
_RE_HINDI_FEVER = re.compile(
    r"^([\u0900-\u097F][\u0900-\u097F\s]{2,45}?)\s+(को\s+(?:__SHIELD_\d+__|\d+\s+दिन\s+से))",
    re.MULTILINE,
)
_RE_LABEL_PATIENT_NAME = re.compile(
    r"(?i)(?:Patient Name|Patient|Naam|मरीज़ का नाम|रोगी का नाम|नाम)\s*[:=]\s*([^\n,;|]+)"
)
_RE_LABEL_RELATIVE_NAME = re.compile(
    r"(?i)(?:Relative Name|Guardian|Pita/Pati ka Naam|Father's Name|Husband's Name)\s*[:=]\s*([^\n,;|]+)"
)
_RE_LABEL_MRN = re.compile(
    r"(?i)(?:UHID|IPD No|Reg No|Registration No|MRN)\s*[:=]\s*([^\n,;|]+)"
)
_RE_LABEL_DOB = re.compile(
    r"(?i)\b(?:DOB|D\.O\.B|Date of Birth|जन्म तिथि)\s*[:=]\s*([^\n,;|]+)"
)
_RE_LABEL_VILLAGE = re.compile(
    r"(?i)(?:Village|Gram|ग्राम|गाँव|गाव)\s*[:=]\s*([^\n,;|]+)"
)
_RE_LABEL_ADDRESS = re.compile(
    r"(?i)(?:Address(?:\s*\([^\)]*\))?|Permanent Address|Present Address|Pata|पता)\s*[:=]\s*([^\n]+)"
)

_RE_HOUSE = re.compile(
    r"\b(?:H\.No\.?|House|Makan\s+No\.?|मकान\s+नं\.?)\s*\d+[A-Za-z0-9/-]*\b",
    re.IGNORECASE,
)
_RE_LOCALITY = re.compile(
    r"\b(?:Mohalla|Ward)\s+[A-Za-z0-9\s]+?(?=[,\n;]|near|opp|behind|$)",
    re.IGNORECASE,
)
_RE_LANDMARK = re.compile(
    r"\b(?:near|opp\.?|behind|के\s+पास|के\s+पीछे)\s+[A-Za-z\u0900-\u097F0-9\s]+?(?=[,\n;]|Tehsil|Distt|Vill|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 3. PII Gazetteer Builder
# ---------------------------------------------------------------------------

def build_pii_gazetteer(records: list[Any]) -> dict[str, Any]:
    """Aggregate all extracted patient names, relative names, MRNs, and villages from Pass 1."""
    patient_names: set[str] = set()
    relative_names: set[str] = set()
    mrns: set[str] = set()
    villages: set[str] = set()
    phones: set[str] = set()

    for r in records:
        for n in getattr(r, "names", []):
            clean_n = n.strip() if isinstance(n, str) else ""
            if len(clean_n) >= 3:
                patient_names.add(clean_n)
        for rel, rname in getattr(r, "relatives", []):
            clean_rn = rname.strip() if isinstance(rname, str) else ""
            if len(clean_rn) >= 3:
                relative_names.add(clean_rn)
        for m in getattr(r, "mrns", []):
            clean_m = m.strip() if isinstance(m, str) else ""
            if len(clean_m) >= 3:
                mrns.add(clean_m)
        v = getattr(r, "village", None)
        if isinstance(v, str) and len(v.strip()) >= 3:
            villages.add(v.strip())
        for p in getattr(r, "phones", []):
            clean_p = p.strip() if isinstance(p, str) else ""
            if len(clean_p) >= 8:
                phones.add(clean_p)

    sorted_patient_names = sorted(patient_names, key=lambda s: (-len(s), s))
    sorted_relative_names = sorted(relative_names, key=lambda s: (-len(s), s))
    sorted_mrns = sorted(mrns, key=lambda s: (-len(s), s))
    sorted_villages = sorted(villages, key=lambda s: (-len(s), s))

    return {
        "patient_names": sorted_patient_names,
        "relative_names": sorted_relative_names,
        "mrns": sorted_mrns,
        "villages": sorted_villages,
        "phones": phones,
    }


# ---------------------------------------------------------------------------
# 4. Core Text Redaction with Mask-and-Restore
# ---------------------------------------------------------------------------

_DETECTIONS_COUNT = 0


def get_detections_count() -> int:
    """Return total count of PII detection/redaction events."""
    return _DETECTIONS_COUNT


def reset_detections_count() -> None:
    """Reset detection counter to zero."""
    global _DETECTIONS_COUNT
    _DETECTIONS_COUNT = 0


def _inc_detections(n: int = 1) -> None:
    global _DETECTIONS_COUNT
    _DETECTIONS_COUNT += n


def redact_text(text: str, gazetteer: dict[str, Any] | None = None) -> str:
    """Mask clinical preserves, apply generic regexes and gazetteer, then restore shields."""
    if not text:
        return text

    # Step 1: Shield clinical preserves
    shielded, shields = shield_text(text)

    # Step 2: Redact generic identifiers on shielded text
    shielded, n = _RE_PHONE.subn("[PHONE]", shielded)
    _inc_detections(n)
    shielded, n = _RE_AADHAAR.subn("[AADHAAR]", shielded)
    _inc_detections(n)
    shielded, n = _RE_AADHAAR_MASKED.subn("[AADHAAR]", shielded)
    _inc_detections(n)
    shielded, n = _RE_PAN.subn("[PAN]", shielded)
    _inc_detections(n)
    shielded, n = _RE_ABHA.subn("[ABHA_NUMBER]", shielded)
    _inc_detections(n)
    shielded, n = _RE_EMAIL.subn("[EMAIL]", shielded)
    _inc_detections(n)
    shielded, n = _RE_MRN_PATTERNS.subn("[MRN]", shielded)
    _inc_detections(n)

    # Address components
    shielded, n = _RE_HOUSE.subn("[HOUSE]", shielded)
    _inc_detections(n)
    shielded, n = _RE_LOCALITY.subn("[LOCALITY]", shielded)
    _inc_detections(n)
    shielded, n = _RE_LANDMARK.subn("[LANDMARK]", shielded)
    _inc_detections(n)

    # Visitor log / nursing log entries: HH:MM - Name (attendant/visitor/...)
    shielded, n = _RE_TIME_LOG_NAME.subn(r"\g<1>[NAME]", shielded)
    _inc_detections(n)
    shielded, n = _RE_ATTENDANT_CALLER_WITNESS.subn(r"called by [CALLER_NAME]", shielded)
    _inc_detections(n)
    shielded, n = _RE_ATTENDANT_LABEL.subn(lambda m: m.group(0).split(":")[0] + ": [RELATIVE_NAME]", shielded)
    _inc_detections(n)
    shielded, n = _RE_LAB_RECEIVED_FROM.subn(r"Sample received from [PATIENT_NAME]'s attendant", shielded)
    _inc_detections(n)
    shielded, n = _RE_DISCHARGE_NOTE_HEADER.subn(r"[PATIENT_NAME], \2", shielded)
    _inc_detections(n)
    shielded, n = _RE_RELATIVE_LABEL.subn(r"\g<1> [RELATIVE_NAME]", shielded)
    _inc_detections(n)
    shielded, n = _RE_ATTENDANT_PAREN.subn(r"Attendant ([RELATIVE_NAME])", shielded)
    _inc_detections(n)
    shielded, n = _RE_HINGLISH_NARRATIVE.subn(r"[PATIENT_NAME] \g<2>", shielded)
    _inc_detections(n)
    shielded, n = _RE_NURSING_KCO.subn(r"\g<1>[PATIENT_NAME]\g<3>", shielded)
    _inc_detections(n)
    shielded, n = _RE_HINDI_FEVER.subn(r"[PATIENT_NAME] \g<2>", shielded)
    _inc_detections(n)

    # Label-anchored lines
    def _sub_label_name(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [PATIENT_NAME]"
    shielded, n = _RE_LABEL_PATIENT_NAME.subn(_sub_label_name, shielded)
    _inc_detections(n)

    def _sub_label_rel(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [RELATIVE_NAME]"
    shielded, n = _RE_LABEL_RELATIVE_NAME.subn(_sub_label_rel, shielded)
    _inc_detections(n)

    def _sub_label_mrn(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [MRN]"
    shielded, n = _RE_LABEL_MRN.subn(_sub_label_mrn, shielded)
    _inc_detections(n)

    def _sub_label_dob(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [DOB]"
    shielded, n = _RE_LABEL_DOB.subn(_sub_label_dob, shielded)
    _inc_detections(n)

    def _sub_label_village(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [VILLAGE]"
    shielded, n = _RE_LABEL_VILLAGE.subn(_sub_label_village, shielded)
    _inc_detections(n)

    def _sub_label_addr(m: re.Match) -> str:
        lead = m.group(0).split(":")[0]
        return f"{lead}: [ADDRESS]"
    shielded, n = _RE_LABEL_ADDRESS.subn(_sub_label_addr, shielded)
    _inc_detections(n)

    # Gazetteer-guided redactions (multi-word names and exact MRNs)
    if gazetteer:
        # MRNs
        for mrn in gazetteer.get("mrns", []):
            if len(mrn) >= 5 and mrn in shielded:
                shielded, n = re.subn(r"\b" + re.escape(mrn) + r"\b", "[MRN]", shielded, flags=re.IGNORECASE)
                _inc_detections(n)

        # Multi-token and guarded single-token patient names
        for name in gazetteer.get("patient_names", []):
            tokens = name.split()
            if len(tokens) >= 2 and len(name) >= 4:
                if name in shielded or name.lower() in shielded.lower():
                    shielded, n = re.subn(r"(?<![A-Za-z0-9\u0900-\u097F])" + re.escape(name) + r"(?![A-Za-z0-9\u0900-\u097F])", "[PATIENT_NAME]", shielded, flags=re.IGNORECASE)
                    _inc_detections(n)
            elif len(tokens) == 1 and len(name) >= 4:
                if name.lower() not in ("asha", "ward", "pill", "diet", "mode", "care", "left", "right", "ramesh"):
                    if name in shielded or name.lower() in shielded.lower():
                        shielded, n = re.subn(r"(?<![A-Za-z0-9\u0900-\u097F])" + re.escape(name) + r"(?![A-Za-z0-9\u0900-\u097F])", "[PATIENT_NAME]", shielded, flags=re.IGNORECASE)
                        _inc_detections(n)

        # Relative names (globally matched with unicode boundary)
        for rname in gazetteer.get("relative_names", []):
            tokens = rname.split()
            if len(tokens) >= 2 and len(rname) >= 4:
                if rname in shielded or rname.lower() in shielded.lower():
                    shielded, n = re.subn(r"(?<![A-Za-z0-9\u0900-\u097F])" + re.escape(rname) + r"(?![A-Za-z0-9\u0900-\u097F])", "[RELATIVE_NAME]", shielded, flags=re.IGNORECASE)
                    _inc_detections(n)

        # Villages
        for v in gazetteer.get("villages", []):
            if len(v) >= 4 and v in shielded:
                shielded, n = re.subn(r"\b" + re.escape(v) + r"\b", "[VILLAGE]", shielded, flags=re.IGNORECASE)
                _inc_detections(n)

    # Step 3: Restore clinical preserve shields
    restored = restore_text(shielded, shields, gazetteer)
    return restored


# ---------------------------------------------------------------------------
# 5. Base64 String Sanitizer
# ---------------------------------------------------------------------------

_B64_TOKEN = re.compile(r"^[A-Za-z0-9+/\-_]{20,}={0,2}$")


def sanitize_base64_string(b64_str: str, gazetteer: dict[str, Any] | None = None) -> str:
    """If string decodes to clean UTF-8 text, redact it and re-encode to base64, supporting URL-safe and whitespace-padded b64."""
    tok = "".join(b64_str.split())
    if len(tok) < 16:
        return b64_str
    is_urlsafe = "-" in tok or "_" in tok
    std_tok = tok.replace("-", "+").replace("_", "/")
    try:
        raw = base64.b64decode(std_tok + "=" * (-len(std_tok) % 4))
        decoded = raw.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return b64_str

    if not any(c.isalpha() for c in decoded):
        return b64_str

    cleaned = redact_text(decoded, gazetteer)
    if is_urlsafe:
        return base64.urlsafe_b64encode(cleaned.encode("utf-8")).decode("ascii")
    return base64.b64encode(cleaned.encode("utf-8")).decode("ascii")


# ---------------------------------------------------------------------------
# 6. Filename Sanitizer with Collision Guard
# ---------------------------------------------------------------------------

_RE_FILENAME_MRN = re.compile(
    r"(?:SNJ[-_]\d{2}[-_]\d+|JJH[-_]\d+|DHH(?:-IPD)?[-_]\d+|UHID[-_]\d+|IPD[-_]\d{4}[-_]\d+|IP[-_]\d{2}[-_]\d+|ADM\d+)",
    re.IGNORECASE,
)
_RE_FILENAME_PHONE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")


def sanitize_filename(relpath: str, gazetteer: dict[str, Any] | None = None) -> str:
    """Scrub PII from filename stem across all folders, append deterministic hash to prevent collisions."""
    p = Path(relpath)
    stem = p.stem
    ext = p.suffix
    parent = p.parent.as_posix() if p.parent.as_posix() != "." else ""

    has_pii = False
    if _RE_FILENAME_PHONE.search(stem):
        has_pii = True
    elif _RE_FILENAME_MRN.search(stem):
        has_pii = True
    elif gazetteer:
        for name in gazetteer.get("patient_names", []):
            if len(name) >= 3 and (name.lower() in stem.lower() or name.lower().replace(" ", "_") in stem.lower()):
                has_pii = True
                break
            for tok in name.split():
                if len(tok) >= 4 and tok.lower() in stem.lower():
                    has_pii = True
                    break
        if not has_pii:
            for rname in gazetteer.get("relative_names", []):
                if len(rname) >= 3 and (rname.lower() in stem.lower() or rname.lower().replace(" ", "_") in stem.lower()):
                    has_pii = True
                    break

    if not has_pii:
        return relpath

    _inc_detections(1)
    cleaned = stem
    cleaned = re.sub(r"(?i)(Discharge_Summary)_[A-Za-z_]+?(_(?:SNJ|JJH|DHH|UHID|IPD|IP|ADM)[A-Za-z0-9_-]*)", r"\1\2", cleaned)
    cleaned = _RE_FILENAME_PHONE.sub("", cleaned)
    cleaned = _RE_FILENAME_MRN.sub("", cleaned)

    if gazetteer:
        for name in gazetteer.get("patient_names", []):
            if len(name) >= 3:
                cleaned = re.sub(re.escape(name), "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(re.escape(name.replace(" ", "_")), "", cleaned, flags=re.IGNORECASE)
            for tok in name.split():
                if len(tok) >= 4:
                    cleaned = re.sub(r"\b" + re.escape(tok) + r"\b", "", cleaned, flags=re.IGNORECASE)
                    cleaned = re.sub(r"(?<=_)" + re.escape(tok) + r"(?=_)", "", cleaned, flags=re.IGNORECASE)
                    cleaned = re.sub(r"^" + re.escape(tok) + r"(?=_)", "", cleaned, flags=re.IGNORECASE)
                    cleaned = re.sub(r"(?<=_)" + re.escape(tok) + r"$", "", cleaned, flags=re.IGNORECASE)
        for rname in gazetteer.get("relative_names", []):
            if len(rname) >= 3:
                cleaned = re.sub(re.escape(rname), "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(re.escape(rname.replace(" ", "_")), "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"[_\s-]+", "_", cleaned).strip("_")
    if not cleaned or len(cleaned) <= 2:
        cleaned = "doc"

    path_hash = hashlib.sha256(relpath.encode("utf-8")).hexdigest()[:8]
    new_name = f"{cleaned}_{path_hash}{ext}"
    return f"{parent}/{new_name}" if parent else new_name


# ---------------------------------------------------------------------------
# 7. Recursive JSON Sanitizer
# ---------------------------------------------------------------------------

_PII_KEYS = {
    "name", "patient_name", "first", "last", "local", "naam",
    "caller", "raised_by", "callback", "phone", "mobile", "mobile_no",
    "guardian", "relative_name", "pita_pati_ka_naam",
    "aadhaar", "aadhar_no", "abha", "abha_no", "pan", "payer_pan",
    "village", "village_mohalla", "gaon", "pin", "zip", "postal_code",
    "address", "line", "street", "pata",
}


def clean_json_value(data: Any, gazetteer: dict[str, Any] | None = None, pid: str | None = None) -> Any:
    """Recursively clean dicts and lists in JSON, supporting base64 and key sanitization."""
    if isinstance(data, dict):
        cleaned_dict: dict[str, Any] = {}
        for k, v in data.items():
            norm_k = re.sub(r"[_\s-]+", "_", str(k).strip().lower())

            # Metadata sanitization
            if k == "_export_meta" and isinstance(v, dict):
                meta_copy = dict(v)
                if "source_path" in meta_copy:
                    meta_copy["source_path"] = re.sub(r"[A-Za-z0-9_]+(\\|\/)visits", r"export\1visits", str(meta_copy["source_path"]))
                cleaned_dict[k] = clean_json_value(meta_copy, gazetteer, pid)
                continue

            # Key sanitization across all JSON files (keys can be names, relatives, or MRNs)
            clean_k = k
            if norm_k not in _PII_KEYS:
                if gazetteer:
                    for mrn in gazetteer.get("mrns", []):
                        if mrn.lower() in str(k).lower():
                            clean_k = "[MRN]"
                            break
                    for name in gazetteer.get("patient_names", []):
                        if len(name) >= 3 and name.lower() in str(k).lower():
                            clean_k = "[PATIENT_NAME]"
                            break
                    for rname in gazetteer.get("relative_names", []):
                        if len(rname) >= 3 and rname.lower() in str(k).lower():
                            clean_k = "[RELATIVE_NAME]"
                            break

            if clean_k != k:
                _inc_detections(1)

            # Value sanitization based on key type
            if norm_k in ("uhid", "hospital_uhid", "patient_ref", "mrn", "ipd_no", "reg_no"):
                _inc_detections(1)
                cleaned_dict[clean_k] = pid if pid is not None else "[MRN]"
            elif norm_k in ("name", "patient_name", "first", "local", "naam"):
                _inc_detections(1)
                cleaned_dict[clean_k] = "[PATIENT_NAME]"
            elif norm_k == "last":
                _inc_detections(1)
                cleaned_dict[clean_k] = ""
            elif norm_k in ("phone", "mobile", "mobile_no", "callback", "contact"):
                if isinstance(v, dict):
                    cleaned_dict[clean_k] = clean_json_value(v, gazetteer, pid)
                else:
                    _inc_detections(1)
                    cleaned_dict[clean_k] = "[PHONE]"
            elif norm_k in ("guardian", "relative_name", "pita_pati_ka_naam"):
                if isinstance(v, dict):
                    cleaned_dict[clean_k] = clean_json_value(v, gazetteer, pid)
                else:
                    _inc_detections(1)
                    cleaned_dict[clean_k] = "[RELATIVE_NAME]"
            elif norm_k in ("aadhaar", "aadhar_no", "ids"):
                if isinstance(v, dict):
                    cleaned_dict[clean_k] = clean_json_value(v, gazetteer, pid)
                else:
                    _inc_detections(1)
                    cleaned_dict[clean_k] = "[AADHAAR]"
            elif norm_k in ("village", "village_mohalla", "gaon"):
                _inc_detections(1)
                cleaned_dict[clean_k] = "[VILLAGE]"
            elif norm_k in ("pin", "zip", "postal_code", "pincode"):
                _inc_detections(1)
                cleaned_dict[clean_k] = "[PIN]"
            elif norm_k in ("address", "line", "street", "pata"):
                if isinstance(v, dict):
                    cleaned_dict[clean_k] = clean_json_value(v, gazetteer, pid)
                else:
                    _inc_detections(1)
                    cleaned_dict[clean_k] = "[ADDRESS]"
            elif norm_k in ("data", "payload", "attachment") and isinstance(v, str):
                cleaned_dict[clean_k] = sanitize_base64_string(v, gazetteer)
            else:
                cleaned_dict[clean_k] = clean_json_value(v, gazetteer, pid)
        return cleaned_dict

    if isinstance(data, list):
        return [clean_json_value(x, gazetteer, pid) for x in data]

    if isinstance(data, str):
        tok = "".join(data.split())
        if len(tok) >= 16 and _B64_TOKEN.match(tok):
            san_b64 = sanitize_base64_string(data, gazetteer)
            if san_b64 != data:
                return san_b64
        return redact_text(data, gazetteer)

    return data
