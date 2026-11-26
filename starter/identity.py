"""Phase 1-5 patient identity extraction, normalization, blocking, clustering, HMAC keys, and temporal helpers (stdlib only)."""
from __future__ import annotations

import base64
import binascii
import datetime as dt
import difflib
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Devanagari digits (०-९) -> ASCII digits (0-9) for numeric/ID parsing.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Regex patterns for unstructured and semi-structured text extraction.
_RE_ABHA = re.compile(r"(?<![A-Za-z0-9/-])\d{2}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}(?![\d-])")
_RE_AADHAAR_12 = re.compile(r"(?<![A-Za-z0-9/-])\d{4}[\s-]?\d{4}[\s-]?\d{4}(?![\d-])")
_RE_AADHAAR_MASKED = re.compile(r"(?i)(?<![A-Za-z0-9])X{4}[\s-]?X{4}[\s-]?\d{4}(?!\d)")
_RE_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])")
_RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_RE_PHONE = re.compile(
    r"(?<![A-Za-z0-9/-])(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?![\d-])"
)
_RE_MRN_INLINE = re.compile(
    r"\b(?:SNJ/\d{2}/\d{4,}|JJH-\d{5,}|DHH\d{4}-\d{4,}|DHH-IPD-\d{4,}"
    r"|UHID-\d{4,}|IPD-\d{4}-\d{4,}|IP/\d{2}/\d{4,}|ADM\d{5,})\b"
)
_RE_PIN = re.compile(r"(?:[-–:]|\bPIN[:\s-]*)\s*(\d{6})\b", re.IGNORECASE)
_RE_PIN_BARE = re.compile(r"^\d{6}$")

_RE_REL_PAREN_OR_INLINE = re.compile(
    r"\b([SDWC]/[Oo])\s+([^\n,();|\[\]]{2,50})"
)
_RE_DISCHARGE_NOTE_LEAD = re.compile(
    r"^[ \t]*([A-Z][a-zA-Z. ]{1,40}?),\s*([SDWC]/[Oo])\s+([^\n,;]{2,50}?)(?:,|$)",
    re.MULTILINE,
)
_RE_WHATSAPP_REPORT = re.compile(
    r"Report of\s+(.+?)\s{2,}(\d{1,3})\s*(?:Y(?:rs?)?)?\s*/\s*([MFO])\b",
    re.IGNORECASE,
)
_RE_HINGLISH_NARRATIVE = re.compile(
    r"(?:^|\s-\s|\n)([A-Z][a-zA-Z.]{1,30}(?:\s+(?:sahab|ji|behenji|bhai|babu|didi|amma|[A-Z][a-zA-Z.]{1,25}))?)"
    r"\s+(?:ka|ki|ke)\s+(?:BP|condition)\b",
    re.MULTILINE,
)
_RE_WHATSAPP_SENDER = re.compile(
    r"^\[\d{1,2}/\d{1,2}/\d{2,4},\s*[^\]]+\]\s*([^:\n]{2,50}?):\s*",
    re.MULTILINE,
)
_RE_NURSING_KCO = re.compile(
    r"^\d{1,2}:\d{2}\s*-\s*([A-Z][a-zA-Z. ]{2,45}?)\s*-\s*K/C/O\b",
    re.MULTILINE,
)
_RE_HINDI_FEVER_LINE = re.compile(
    r"^([\u0900-\u097F][\u0900-\u097F\s]{2,45}?)\s+को\s+\d+\s+दिन\s+से\b",
    re.MULTILINE,
)
_RE_VILLAGE_TEXT = re.compile(
    r"(?:Vill\.?|Gram|ग्राम)\s+([^,\n-]+)",
    re.IGNORECASE,
)
_RE_TEHSIL_BEFORE = re.compile(
    r",\s*([^,\n]+?)\s*,\s*(?:Tehsil|तहसील)\b",
    re.IGNORECASE,
)
_RE_AGE_SEX = re.compile(
    r"(\d{1,3})\s*(?:Y(?:rs?|ears?)?|वर्ष)?\s*(?:/\s*([MFO]|male|female|other|पुरुष|महिला|अन्य))?",
    re.IGNORECASE,
)
_RE_DOCTOR_PREFIX = re.compile(r"^\s*Dr\.?\s", re.IGNORECASE)
_RE_LINE_SPLIT = re.compile(r"\s{2,}|\t+")

_ADDR_NOISE_PREFIXES = (
    "house",
    "h.no",
    "makan",
    "मकान",
    "ward",
    "mohalla",
    "मोहल्ला",
    "near",
    "opp",
    "behind",
    "c/o",
)
_ADDR_LANDMARK_FRAGMENTS = (
    "के पास",
    "के पीछे",
    "के सामने",
    "mandir",
    "masjid",
    "gurudwara",
    "temple",
    "school",
    "bhawan",
)

# Canonical alias sets for normalized CSV/TSV/JSON keys.
_KEY_ALIASES: dict[str, str] = {
    "patient_name": "name",
    "name": "name",
    "full_name": "name",
    "naam": "name",
    "first_name": "first_name",
    "firstname": "first_name",
    "last_name": "last_name",
    "lastname": "last_name",
    "phone": "phone",
    "mobile": "phone",
    "mobile_no": "phone",
    "mob": "phone",
    "telephone": "phone",
    "tel": "phone",
    "email": "email",
    "e_mail": "email",
    "mrn": "mrn",
    "uhid": "mrn",
    "reg_no": "mrn",
    "registration_no": "mrn",
    "patient_id": "mrn",
    "patientid": "mrn",
    "patient_ref": "mrn",
    "hospital_uhid": "mrn",
    "ipd_no": "mrn",
    "abha": "abha",
    "abha_no": "abha",
    "abha_number": "abha",
    "aadhaar": "aadhaar",
    "aadhaar_no": "aadhaar",
    "aadhar": "aadhaar",
    "aadhar_no": "aadhaar",
    "pan": "pan",
    "payer_pan": "pan",
    "pan_no": "pan",
    "insurance_id": "insurance",
    "pmjay_id": "insurance",
    "policy_no": "insurance",
    "policy_number": "insurance",
    "dob": "dob",
    "birthdate": "dob",
    "birth_date": "dob",
    "janm_tithi": "dob",
    "date_of_birth": "dob",
    "age": "age",
    "umar": "age",
    "gender": "gender",
    "sex": "gender",
    "ling": "gender",
    "age_sex": "age_sex",
    "age_gender": "age_sex",
    "relation": "relation",
    "relative_name": "relative_name",
    "guardian": "guardian",
    "relative": "guardian",
    "pita_pati_ka_naam": "guardian",
    "father_name": "father_name",
    "husband_name": "husband_name",
    "mother_name": "mother_name",
    "village": "village",
    "village_mohalla": "village",
    "gaon": "village",
    "city": "village",
    "pin": "pin",
    "pin_code": "pin",
    "pincode": "pin",
    "zip": "pin",
    "postal_code": "pin",
    "postalcode": "pin",
    "address": "address",
    "address_note": "address",
    "pata": "address",
    "street": "address",
    "notes": "notes",
    "remarks": "notes",
}

_GENDER_MAP: dict[str, str] = {
    "m": "M",
    "male": "M",
    "पुरुष": "M",
    "f": "F",
    "female": "F",
    "महिला": "F",
    "o": "O",
    "other": "O",
    "अन्य": "O",
}

_HOSPITAL_MARKERS: tuple[tuple[str, str], ...] = (
    ("sanjeevani", "sanjeevani"),
    ("snj", "sanjeevani"),
    ("ipd-2026-", "sanjeevani"),
    ("jeevanjyoti", "jeevanjyoti"),
    ("jeevan jyoti", "jeevanjyoti"),
    ("jjh", "jeevanjyoti"),
    ("ip/26/", "jeevanjyoti"),
    ("arogya", "arogya"),
    ("dhhardoi", "dhhardoi"),
    ("district hospital hardoi", "dhhardoi"),
    ("dhh", "dhhardoi"),
    ("uhid-", "arogya"),
    ("adm0", "arogya"),
)


@dataclass
class RecordIdentity:
    """Raw identity attributes extracted from one hospital record before redaction."""

    relpath: str
    loc: str
    hospital_hint: str | None = None
    names: list[str] = field(default_factory=list)
    relatives: list[tuple[str, str]] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    mrns: list[str] = field(default_factory=list)
    abha: list[str] = field(default_factory=list)
    aadhaar: list[str] = field(default_factory=list)
    pan: list[str] = field(default_factory=list)
    insurance_ids: list[str] = field(default_factory=list)
    dob: str | None = None
    age: int | None = None
    gender: str | None = None
    village: str | None = None
    pin: str | None = None


class IdentityResolver:
    """Patient identity resolver using blocking, conflict vetoes, and conflict-safe clustering.

    Pass 2 Handoff API (for Member 1 & Member 3 integration):
      * ``add_record(relpath, loc, raw_fields, raw_text="")`` — register raw records in Pass 1.
      * ``resolve_all()`` — normalize, cluster, and compute per-cluster pseudonyms & day shifts.
      * ``get_patient_id(relpath, loc)`` -> ``'PSN-XXXXXX'`` or ``None`` for non-patient/unlinked.
      * ``get_date_shift_days(relpath, loc)`` -> deterministic non-zero ``int`` day offset or ``None``.
      * ``shift_patient_date(relpath, loc, value)`` -> ``dt.date`` shifted by the cluster's offset.
      * ``get_patient_key(relpath, loc)`` -> internal canonical key ONLY for Pass 2 helpers (never log/serialize).
    """

    def __init__(
        self,
        pseudonym_fn: Callable[[str], str] | None = None,
        date_shift_fn: Callable[[str], int] | None = None,
    ) -> None:
        self._pseudonym_fn = pseudonym_fn
        self._date_shift_fn = date_shift_fn
        self.records: list[RecordIdentity] = []
        self.normalized: list[NormalizedIdentity] = []
        self.clusters: list[list[int]] = []
        self._by_locator: dict[tuple[str, str], RecordIdentity] = {}
        self._patient_ids: dict[tuple[str, str], str] = {}
        self._patient_keys: dict[tuple[str, str], str] = {}
        self._date_shifts: dict[tuple[str, str], int] = {}

    def add_record(
        self, relpath: str, loc: str, raw_fields: dict, raw_text: str = ""
    ) -> None:
        """Register a record during Pass 1 (before redaction)."""
        rec = extract_identity(relpath, loc, raw_fields, raw_text)
        self.records.append(rec)
        self._by_locator[(relpath, loc)] = rec

    def resolve_all(
        self,
        pseudonym_fn: Callable[[str], str] | None = None,
        date_shift_fn: Callable[[str], int] | None = None,
    ) -> None:
        """Run normalization, blocking, scoring, conflict-safe clustering, and compute PSN IDs."""
        fn = pseudonym_fn if pseudonym_fn is not None else self._pseudonym_fn
        shift_fn = date_shift_fn if date_shift_fn is not None else self._date_shift_fn
        self.normalized = [normalize_identity(r) for r in self.records]
        self._patient_ids.clear()
        self._patient_keys.clear()
        self._date_shifts.clear()
        self.clusters = []

        n = len(self.normalized)
        if n == 0:
            return

        candidate_pairs = generate_candidate_pairs(self.normalized)
        scored_edges: list[tuple[float, int, int]] = []
        for i, j in candidate_pairs:
            sc = score_pair(self.normalized[i], self.normalized[j])
            if sc >= LINK_THRESHOLD:
                scored_edges.append((sc, i, j))

        scored_edges.sort(key=lambda item: (-item[0], item[1], item[2]))

        parent = list(range(n))
        members: dict[int, list[int]] = {i: [i] for i in range(n)}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        conflict_cache: dict[tuple[int, int], bool] = {}

        def pair_conflicts(x: int, y: int) -> bool:
            key = (x, y) if x < y else (y, x)
            cached = conflict_cache.get(key)
            if cached is None:
                cached = has_hard_conflict(self.normalized[x], self.normalized[y])
                conflict_cache[key] = cached
            return cached

        for _, i, j in scored_edges:
            ri, rj = find(i), find(j)
            if ri == rj:
                continue
            # Conflict-safe cluster guard: reject merge if any cross-pair has a hard conflict.
            if any(pair_conflicts(x, y) for x in members[ri] for y in members[rj]):
                continue
            if len(members[ri]) < len(members[rj]):
                ri, rj = rj, ri
            parent[rj] = ri
            members[ri].extend(members[rj])
            del members[rj]

        all_components = [sorted(idxs) for idxs in members.values()]
        linked_clusters = [idxs for idxs in all_components if len(idxs) >= 2]
        linked_clusters.sort(
            key=lambda idxs: min((self.records[idx].relpath, self.records[idx].loc) for idx in idxs)
        )
        self.clusters = linked_clusters

        if fn is None:
            for cid, idxs in enumerate(linked_clusters):
                label = f"cluster_{cid}"
                for idx in idxs:
                    rec = self.records[idx]
                    self._patient_ids[(rec.relpath, rec.loc)] = label
            return

        # Phase 4 mode: assign deterministic patient_key and HMAC PSN-XXXXXX to all patient
        # clusters (multi-record clusters plus patient singletons with name or strong ID).
        patient_clusters = [
            idxs
            for idxs in all_components
            if len(idxs) >= 2
            or bool(self.normalized[idxs[0]].norm_names or self.normalized[idxs[0]].strong_ids)
        ]
        patient_clusters.sort(
            key=lambda idxs: min((self.records[idx].relpath, self.records[idx].loc) for idx in idxs)
        )

        raw_keys = [
            _build_cluster_key([self.normalized[idx] for idx in idxs]) for idxs in patient_clusters
        ]
        key_counts = Counter(raw_keys)

        for idxs, base_key in zip(patient_clusters, raw_keys):
            patient_key = base_key
            if key_counts[base_key] > 1:
                # Guarantee distinct clusters (e.g. shared family phone veto) never share a PSN.
                min_rel, min_loc = min(
                    (self.records[idx].relpath, self.records[idx].loc) for idx in idxs
                )
                patient_key = f"{base_key}|at:{min_rel}|{min_loc}"
            pid = fn(patient_key)
            shift_days = shift_fn(patient_key) if shift_fn is not None else None
            for idx in idxs:
                rec = self.records[idx]
                loc_key = (rec.relpath, rec.loc)
                self._patient_ids[loc_key] = pid
                self._patient_keys[loc_key] = patient_key
                if shift_days is not None:
                    self._date_shifts[loc_key] = shift_days

    def get_patient_id(self, relpath: str, loc: str) -> str | None:
        """Return 'PSN-XXXXXX' or None if unlinked."""
        return self._patient_ids.get((relpath, loc))

    def get_patient_key(self, relpath: str, loc: str) -> str | None:
        """Return internal canonical key ONLY for shift_date() in Pass 2. Never serialize."""
        return self._patient_keys.get((relpath, loc))

    def get_date_shift_days(self, relpath: str, loc: str) -> int | None:
        """Return the cluster's deterministic non-zero day offset, or None for non-patient records."""
        return self._date_shifts.get((relpath, loc))

    def shift_patient_date(self, relpath: str, loc: str, value: dt.date) -> dt.date:
        """Shift a patient date by the cluster's exact offset (no-op if non-patient/unlinked)."""
        offset = self._date_shifts.get((relpath, loc))
        if offset is None:
            return value
        return value + dt.timedelta(days=offset)


def _ascii_digits(text: str) -> str:
    return text.translate(_DEVANAGARI_DIGITS)


def _append_unique(items: list[Any], value: Any) -> None:
    if value and value not in items:
        items.append(value)


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _normalize_key(key: str) -> str:
    no_parens = re.sub(r"\([^)]*\)", "", key)
    return re.sub(r"[^a-z0-9]+", "_", no_parens.strip().lower()).strip("_")


def _parse_gender(val: str) -> str | None:
    cleaned = _clean_str(val).lower()
    if not cleaned:
        return None
    if cleaned in _GENDER_MAP:
        return _GENDER_MAP[cleaned]
    for token in re.split(r"[\s/,]+", cleaned):
        if token in _GENDER_MAP:
            return _GENDER_MAP[token]
    return None


def _parse_age_sex(val: str) -> tuple[int | None, str | None]:
    ascii_val = _ascii_digits(_clean_str(val))
    if not ascii_val:
        return None, None
    m = _RE_AGE_SEX.search(ascii_val)
    if not m:
        return None, _parse_gender(ascii_val)
    age_num = int(m.group(1))
    age = age_num if 0 <= age_num <= 125 else None
    gender = _parse_gender(m.group(2)) if m.group(2) else None
    if gender is None and "/" in ascii_val:
        gender = _parse_gender(ascii_val.split("/", 1)[1])
    return age, gender


def _parse_guardian(val: str, default_rel: str = "Guardian") -> tuple[str, str] | None:
    s = _clean_str(val)
    if not s:
        return None
    m = re.match(r"^\s*([SDWC]/[Oo])\s+(.+)$", s)
    if m:
        return (m.group(1), m.group(2).strip(" ,.;"))
    return (default_rel, s.strip(" ,.;"))


def _add_name(rec: RecordIdentity, val: str) -> None:
    name = _clean_str(val).strip(" ,.;")
    if name and not _RE_DOCTOR_PREFIX.match(name):
        _append_unique(rec.names, name)


def _add_relative(rec: RecordIdentity, relation: str, name: str) -> None:
    rel = _clean_str(relation)
    person = _clean_str(name).strip(" ,.;")
    if rel and person and not _RE_DOCTOR_PREFIX.match(person):
        _append_unique(rec.relatives, (rel, person))


def _extract_inline_ids(text: str, rec: RecordIdentity) -> None:
    """Extract phones, emails, ABHA, Aadhaar, PAN, and MRNs from free text."""
    ascii_text = _ascii_digits(text)
    for m in _RE_EMAIL.finditer(text):
        _append_unique(rec.emails, m.group(0))
    for m in _RE_PAN.finditer(ascii_text):
        _append_unique(rec.pan, m.group(0))

    # Extract longer numeric IDs (14-digit ABHA, 12-digit Aadhaar) before 10-digit phones.
    abha_spans: list[tuple[int, int]] = []
    for m in _RE_ABHA.finditer(ascii_text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 14:
            _append_unique(rec.abha, m.group(0).strip())
            abha_spans.append(m.span())

    for m in _RE_AADHAAR_12.finditer(ascii_text):
        if any(s <= m.start() and m.end() <= e for s, e in abha_spans):
            continue
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) == 12:
            _append_unique(rec.aadhaar, m.group(0).strip())

    for m in _RE_AADHAAR_MASKED.finditer(ascii_text):
        _append_unique(rec.aadhaar, m.group(0).strip())

    for m in _RE_PHONE.finditer(ascii_text):
        if any(s <= m.start() and m.end() <= e for s, e in abha_spans):
            continue
        _append_unique(rec.phones, m.group(0).strip())

    for m in _RE_MRN_INLINE.finditer(ascii_text):
        _append_unique(rec.mrns, m.group(0).strip())


def _extract_address_details(addr_text: str, rec: RecordIdentity) -> None:
    """Extract C/O relatives, village, and PIN from an address string."""
    s = _clean_str(addr_text)
    if not s:
        return
    # C/O in address context means care-of relative (never clinical "C/O fever").
    co_match = re.match(r"^\s*C/[Oo]\s+([^,\n]+)", s)
    if co_match:
        _add_relative(rec, "C/o", co_match.group(1))

    if rec.pin is None:
        ascii_s = _ascii_digits(s)
        m_pin = _RE_PIN.search(ascii_s)
        if m_pin:
            rec.pin = m_pin.group(1)

    if rec.village is None:
        m_v = _RE_VILLAGE_TEXT.search(s)
        if m_v:
            rec.village = m_v.group(1).strip()
        else:
            m_t = _RE_TEHSIL_BEFORE.search(s)
            if m_t:
                cand = m_t.group(1).strip()
                low_cand = cand.lower()
                if (
                    cand
                    and not low_cand.startswith(_ADDR_NOISE_PREFIXES)
                    and not any(frag in low_cand for frag in _ADDR_LANDMARK_FRAGMENTS)
                ):
                    rec.village = cand


def _extract_from_fhir(data: dict, rec: RecordIdentity) -> None:
    """Extract patient identity from a FHIR R4 Bundle or Patient resource."""
    rtype = data.get("resourceType")
    if rtype == "Bundle":
        meta_src = _clean_str((data.get("meta") or {}).get("source"))
        if meta_src.startswith("#") and len(meta_src) > 1:
            _append_unique(rec.mrns, meta_src[1:].strip())
        resources = [
            e.get("resource", {})
            for e in (data.get("entry") or [])
            if isinstance(e, dict) and isinstance(e.get("resource"), dict)
        ]
    elif rtype == "Patient":
        resources = [data]
    else:
        return

    for res in resources:
        rt = res.get("resourceType")
        if rt == "Patient":
            _extract_fhir_patient(res, rec)
        elif rt == "Encounter":
            for ident in res.get("identifier") or []:
                val = _clean_str(ident.get("value"))
                if val:
                    _append_unique(rec.mrns, val)
        elif rt == "DocumentReference":
            for content in res.get("content") or []:
                att = content.get("attachment") or {}
                if att.get("contentType") == "text/plain" and att.get("data"):
                    decoded = _safe_b64_text(att["data"])
                    if decoded:
                        _extract_from_text(decoded, rec)


def _extract_fhir_patient(patient: dict, rec: RecordIdentity) -> None:
    for ident in patient.get("identifier") or []:
        if not isinstance(ident, dict):
            continue
        val = _clean_str(ident.get("value"))
        if not val:
            continue
        sys_url = _clean_str(ident.get("system")).lower()
        codes = {
            _clean_str(c.get("code")).upper()
            for c in ((ident.get("type") or {}).get("coding") or [])
            if isinstance(c, dict)
        }
        if "healthid" in sys_url or "abdm" in sys_url:
            _append_unique(rec.abha, val)
        elif "aadhaar" in sys_url or "aadhar" in sys_url or "uidai" in sys_url:
            _append_unique(rec.aadhaar, val)
        elif "pan" in sys_url:
            _append_unique(rec.pan, val)
        elif "pmjay" in sys_url or "insurance" in sys_url or "policy" in sys_url:
            _append_unique(rec.insurance_ids, val)
        elif "MR" in codes or "uhid" in sys_url or _RE_MRN_INLINE.match(val):
            _append_unique(rec.mrns, val)

    for nm in patient.get("name") or []:
        if isinstance(nm, dict):
            text_name = _clean_str(nm.get("text"))
            if text_name:
                _add_name(rec, text_name)
            else:
                parts = [_clean_str(g) for g in (nm.get("given") or []) if _clean_str(g)]
                fam = _clean_str(nm.get("family"))
                if fam:
                    parts.append(fam)
                if parts:
                    _add_name(rec, " ".join(parts))

    for tel in patient.get("telecom") or []:
        if not isinstance(tel, dict):
            continue
        sys_t = _clean_str(tel.get("system")).lower()
        val = _clean_str(tel.get("value"))
        if not val:
            continue
        if sys_t == "phone":
            _append_unique(rec.phones, val)
        elif sys_t == "email":
            _append_unique(rec.emails, val)

    if rec.gender is None and patient.get("gender"):
        rec.gender = _parse_gender(str(patient["gender"]))
    if rec.dob is None and patient.get("birthDate"):
        rec.dob = _clean_str(patient["birthDate"])

    for addr in patient.get("address") or []:
        if not isinstance(addr, dict):
            continue
        if rec.village is None and addr.get("city"):
            rec.village = _clean_str(addr["city"])
        if rec.pin is None and addr.get("postalCode"):
            rec.pin = _ascii_digits(_clean_str(addr["postalCode"]))
        for line in addr.get("line") or []:
            _extract_address_details(str(line), rec)

    for contact in patient.get("contact") or []:
        if not isinstance(contact, dict):
            continue
        cname = _clean_str((contact.get("name") or {}).get("text"))
        rels = contact.get("relationship") or []
        rel_label = _clean_str(rels[0].get("text")) if rels and isinstance(rels[0], dict) else "Guardian"
        if cname:
            _add_relative(rec, rel_label or "Guardian", cname)

    for ext in patient.get("extension") or []:
        if not isinstance(ext, dict):
            continue
        url = _clean_str(ext.get("url")).lower()
        val_str = _clean_str(ext.get("valueString"))
        if val_str and "mother" in url:
            _add_relative(rec, "Mother", val_str)


def _extract_structured_dict(fields: dict, rec: RecordIdentity) -> None:
    """Extract raw identity attributes from CSV/TSV rows or non-FHIR JSON objects."""
    first_name = ""
    last_name = ""
    pending_rel_type = ""
    pending_rel_name = ""

    # Handle nested sub-objects used in DHH registration, OPD visits, Lab reports, and Consent forms.
    for nested_key in ("patient", "guardian", "contact", "address", "ids"):
        sub = fields.get(nested_key)
        if isinstance(sub, dict):
            if nested_key == "patient":
                _extract_structured_dict(sub, rec)
            elif nested_key == "guardian":
                rel = _clean_str(sub.get("relation")) or "Guardian"
                gname = _clean_str(sub.get("name"))
                if gname:
                    _add_relative(rec, rel, gname)
            elif nested_key == "address":
                if rec.village is None and sub.get("village"):
                    rec.village = _clean_str(sub["village"])
                if rec.pin is None and sub.get("pin"):
                    rec.pin = _ascii_digits(_clean_str(sub["pin"]))
                if sub.get("line"):
                    _extract_address_details(str(sub["line"]), rec)
            else:
                _extract_structured_dict(sub, rec)

    name_obj = fields.get("name")
    if isinstance(name_obj, dict):
        full = " ".join(
            p for p in (_clean_str(name_obj.get("first")), _clean_str(name_obj.get("last"))) if p
        )
        _add_name(rec, full)
        if name_obj.get("local"):
            _add_name(rec, str(name_obj["local"]))

    att = fields.get("attachment")
    if isinstance(att, dict) and att.get("contentType") == "text/plain" and att.get("data"):
        decoded = _safe_b64_text(str(att["data"]))
        if decoded:
            _extract_from_text(decoded, rec)

    for raw_k, raw_v in fields.items():
        if isinstance(raw_v, (dict, list)) or raw_v is None:
            continue
        val = _clean_str(raw_v)
        if not val:
            continue

        norm_k = _normalize_key(str(raw_k))
        canonical = _KEY_ALIASES.get(norm_k)

        if canonical == "name":
            _add_name(rec, val)
        elif canonical == "first_name":
            first_name = val
        elif canonical == "last_name":
            last_name = val
        elif canonical == "phone":
            _append_unique(rec.phones, _ascii_digits(val))
        elif canonical == "email":
            _append_unique(rec.emails, val)
        elif canonical == "mrn":
            _append_unique(rec.mrns, val)
        elif canonical == "abha":
            _append_unique(rec.abha, _ascii_digits(val))
        elif canonical == "aadhaar":
            _append_unique(rec.aadhaar, _ascii_digits(val))
        elif canonical == "pan":
            _append_unique(rec.pan, val.upper())
        elif canonical == "insurance":
            _append_unique(rec.insurance_ids, val)
        elif canonical == "dob":
            if rec.dob is None:
                rec.dob = _ascii_digits(val)
        elif canonical == "age":
            if rec.age is None:
                age, g = _parse_age_sex(val)
                rec.age = age
                if rec.gender is None and g is not None:
                    rec.gender = g
        elif canonical == "gender":
            if rec.gender is None:
                rec.gender = _parse_gender(val)
        elif canonical == "age_sex":
            age, g = _parse_age_sex(val)
            if rec.age is None and age is not None:
                rec.age = age
            if rec.gender is None and g is not None:
                rec.gender = g
        elif canonical == "relation":
            pending_rel_type = val
        elif canonical == "relative_name":
            pending_rel_name = val
        elif canonical == "guardian":
            parsed = _parse_guardian(val)
            if parsed:
                _add_relative(rec, parsed[0], parsed[1])
        elif canonical == "father_name":
            _add_relative(rec, "Father", val)
        elif canonical == "husband_name":
            _add_relative(rec, "W/o", val)
        elif canonical == "mother_name":
            _add_relative(rec, "Mother", val)
        elif canonical == "village":
            if rec.village is None:
                rec.village = val
        elif canonical == "pin":
            if rec.pin is None:
                ascii_pin = _ascii_digits(val)
                if _RE_PIN_BARE.match(ascii_pin):
                    rec.pin = ascii_pin
        elif canonical == "address":
            _extract_address_details(val, rec)
        elif canonical == "notes":
            _extract_inline_ids(val, rec)
            for m in _RE_REL_PAREN_OR_INLINE.finditer(val):
                _add_relative(rec, m.group(1), m.group(2))
            for m in _RE_HINGLISH_NARRATIVE.finditer(val):
                _add_name(rec, m.group(1))

    if first_name or last_name:
        _add_name(rec, f"{first_name} {last_name}".strip())
    if pending_rel_name:
        _add_relative(rec, pending_rel_type or "Guardian", pending_rel_name)


def _extract_label_segments(text: str, rec: RecordIdentity) -> None:
    """Parse structured `Label: Value` segments across EN/HI discharge & Rx notes."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Capture nursing-note header trailing MRN (e.g. "NURSING NOTES - Ward 8 ... - ADM000161")
        if line.upper().startswith("NURSING NOTES"):
            for m in _RE_MRN_INLINE.finditer(line):
                _append_unique(rec.mrns, m.group(0))

        # Split multi-column text lines separated by 2+ spaces or tabs.
        for seg in _RE_LINE_SPLIT.split(line):
            seg = seg.strip()
            if ":" not in seg:
                continue
            label, val = (p.strip() for p in seg.split(":", 1))
            if not val:
                continue
            low = label.lower()

            if low in ("patient name", "name", "मरीज़ का नाम", "मरीज का नाम", "रोगी का नाम", "नाम"):
                _add_name(rec, val)
            elif low in ("age/sex", "age", "आयु", "उम्र"):
                age, g = _parse_age_sex(val)
                if rec.age is None and age is not None:
                    rec.age = age
                if rec.gender is None and g is not None:
                    rec.gender = g
            elif low in ("uhid", "ipd no", "reg no", "reg. no", "mrn", "पंजीकरण सं.", "आईपीडी सं."):
                _append_unique(rec.mrns, val)
            elif low in ("mobile", "mob", "mob.", "phone", "मोबाइल", "फ़ोन"):
                _append_unique(rec.phones, _ascii_digits(val))
            elif low in ("abha no", "abha", "abha number"):
                _append_unique(rec.abha, _ascii_digits(val))
            elif low in ("dob", "date of birth", "जन्म तिथि"):
                if rec.dob is None:
                    rec.dob = _ascii_digits(val)
            elif low in ("पिता का नाम",):
                _add_relative(rec, "Father", val)
            elif low in ("पति का नाम",):
                _add_relative(rec, "W/o", val)
            elif low in ("माता का नाम",):
                _add_relative(rec, "Mother", val)
            elif low in ("gram", "village", "ग्राम"):
                if rec.village is None:
                    rec.village = val
            elif low in ("address", "पता"):
                _extract_address_details(val, rec)
            elif low == "attendant":
                # e.g. "Attendant: Sukhwinder Singh, Mob. 6004394755"
                att_name = re.split(r",|\bMob\.?\b|\bPhone\b", val, maxsplit=1, flags=re.IGNORECASE)[0]
                if att_name.strip():
                    _add_relative(rec, "Attendant", att_name)


def _extract_from_text(text: str, rec: RecordIdentity) -> None:
    """Extract identity signals from unstructured English, Hindi, or Hinglish text."""
    if not text:
        return

    _extract_label_segments(text, rec)
    _extract_inline_ids(text, rec)

    for m in _RE_REL_PAREN_OR_INLINE.finditer(text):
        rel, rname = m.group(1), m.group(2)
        if rel.upper() == "C/O":
            # Avoid clinical "C/O fever" false positives in unstructured text.
            continue
        _add_relative(rec, rel, rname)

    for m in _RE_DISCHARGE_NOTE_LEAD.finditer(text):
        _add_name(rec, m.group(1))
        _add_relative(rec, m.group(2), m.group(3))

    for m in _RE_WHATSAPP_REPORT.finditer(text):
        _add_name(rec, m.group(1))
        if rec.age is None:
            age_num = int(m.group(2))
            if 0 <= age_num <= 125:
                rec.age = age_num
        if rec.gender is None:
            rec.gender = _parse_gender(m.group(3))

    for m in _RE_WHATSAPP_SENDER.finditer(text):
        _add_relative(rec, "Relative", m.group(1))

    for m in _RE_HINGLISH_NARRATIVE.finditer(text):
        _add_name(rec, m.group(1))

    for m in _RE_NURSING_KCO.finditer(text):
        _add_name(rec, m.group(1))

    for m in _RE_HINDI_FEVER_LINE.finditer(text):
        _add_name(rec, m.group(1))


def _safe_b64_text(data: str) -> str:
    try:
        tok = data.strip()
        raw = base64.b64decode(tok + "=" * (-len(tok) % 4))
        return raw.decode("utf-8", errors="ignore")
    except (binascii.Error, ValueError):
        return ""


def _infer_hospital_hint(
    relpath: str, raw_fields: dict, raw_text: str, mrns: list[str]
) -> str | None:
    """Infer hospital identifier hint from path, metadata, MRN prefixes, or text header."""
    sources: list[str] = [relpath]
    if isinstance(raw_fields, dict):
        meta = raw_fields.get("_export_meta")
        if isinstance(meta, dict) and meta.get("hospital"):
            sources.append(str(meta["hospital"]))
        fac = raw_fields.get("facility")
        if isinstance(fac, dict) and fac.get("name"):
            sources.append(str(fac["name"]))
        if raw_fields.get("_hospital"):
            sources.append(str(raw_fields["_hospital"]))
        if raw_fields.get("resourceType") == "Bundle":
            for e in raw_fields.get("entry") or []:
                res = (e or {}).get("resource") or {}
                if res.get("resourceType") == "Organization" and res.get("name"):
                    sources.append(str(res["name"]))
    sources.extend(mrns)
    if raw_text:
        sources.append("\n".join(raw_text.splitlines()[:4]))

    blob = " ".join(sources).lower()
    for marker, hint in _HOSPITAL_MARKERS:
        if marker in blob:
            return hint

    if isinstance(raw_fields, dict) and raw_fields.get("lab"):
        return _clean_str(raw_fields["lab"]) or None
    return None


def extract_identity(
    relpath: str, loc: str, raw_fields: dict, raw_text: str = ""
) -> RecordIdentity:
    """Extract raw patient identity attributes from structured fields and/or text.

    Supports CSV/TSV row dicts, nested JSON records, FHIR R4 Patient/Bundle dicts,
    and unstructured English/Hindi/Hinglish clinical text.
    """
    rec = RecordIdentity(relpath=relpath, loc=loc)

    if isinstance(raw_fields, dict) and raw_fields:
        rtype = raw_fields.get("resourceType")
        if rtype in ("Bundle", "Patient"):
            _extract_from_fhir(raw_fields, rec)
        else:
            _extract_structured_dict(raw_fields, rec)

    if raw_text:
        _extract_from_text(raw_text, rec)

    rec.hospital_hint = _infer_hospital_hint(relpath, raw_fields or {}, raw_text, rec.mrns)
    return rec


# ---------------------------------------------------------------------------
# Phase 2: Multilingual & Format Normalization
# ---------------------------------------------------------------------------

_NUKTA = "\u093c"
_VIRAMA = "\u094d"
_ANUSVARA = "\u0902"
_CHANDRABINDU = "\u0901"
_VISARGA = "\u0903"

_DEVA_INDEPENDENT_VOWELS: dict[str, str] = {
    "अ": "a",
    "आ": "a",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ऋ": "ri",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऑ": "o",
    "ऍ": "e",
}

_DEVA_MATRAS: dict[str, str] = {
    "ा": "a",
    "ि": "i",
    "ी": "i",
    "ु": "u",
    "ू": "u",
    "ृ": "ri",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ॉ": "o",
    "ॅ": "e",
}

_DEVA_CONSONANTS: dict[str, str] = {
    "क": "k",
    "क़": "q",
    "क" + _NUKTA: "q",
    "ख": "kh",
    "ख़": "kh",
    "ख" + _NUKTA: "kh",
    "ग": "g",
    "ग़": "gh",
    "ग" + _NUKTA: "gh",
    "घ": "gh",
    "ङ": "n",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "ज़": "z",
    "ज" + _NUKTA: "z",
    "झ": "jh",
    "ञ": "n",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ड़": "r",
    "ड" + _NUKTA: "r",
    "ढ": "dh",
    "ढ़": "rh",
    "ढ" + _NUKTA: "rh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "f",
    "फ़": "f",
    "फ" + _NUKTA: "f",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "य़": "y",
    "य" + _NUKTA: "y",
    "र": "r",
    "ल": "l",
    "ळ": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
}

_DEVA_LABIALS = frozenset({"प", "फ", "फ़", "ब", "भ", "म"})

_NAME_TOKEN_ALIASES: dict[str, str] = {
    "md": "mohammad",
    "mohd": "mohammad",
    "muhammad": "mohammad",
    "mohammed": "mohammad",
    "mohamad": "mohammad",
    "muhamad": "mohammad",
}

_LEADING_TITLES = frozenset(
    {"shri", "shree", "sri", "smt", "shrimati", "mr", "mrs", "ms", "miss", "dr", "prof"}
)
_LEADING_OPTIONAL_TITLES = frozenset({"kumari", "km"})
_TRAILING_HONORIFICS = frozenset(
    {
        "ji",
        "sahab",
        "saheb",
        "saab",
        "babu",
        "amma",
        "papa",
        "bhaiya",
        "bhaiyya",
        "bhai",
        "didi",
        "behenji",
        "behanji",
        "bahanji",
        "chacha",
        "chachu",
        "tau",
        "kaka",
        "mama",
        "mausi",
        "bua",
        "bhabhi",
        "dada",
        "dadi",
        "nana",
        "nani",
        "devi",
    }
)

_CLINICAL_TRAP_WORDS = frozenset(
    {
        "fever",
        "cough",
        "pain",
        "headache",
        "diabetes",
        "hypertension",
        "disease",
        "addison",
        "typhoid",
        "malaria",
        "dengue",
        "k/c/o",
    }
)

_REL_CANON_MAP: dict[str, str] = {
    "s/o": "S/o",
    "son of": "S/o",
    "d/o": "D/o",
    "daughter of": "D/o",
    "w/o": "W/o",
    "wife of": "W/o",
    "husband": "W/o",
    "c/o": "C/o",
    "care of": "C/o",
    "father": "Father",
    "mother": "Mother",
    "attendant": "Attendant",
    "relative": "Guardian",
    "guardian": "Guardian",
}


@dataclass
class NormalizedIdentity:
    """Canonicalized patient identity attributes for blocking, scoring, and matching."""

    relpath: str
    loc: str
    hospital_hint: str | None = None
    norm_names: list[str] = field(default_factory=list)
    name_tokens: list[list[str]] = field(default_factory=list)
    name_skeletons: list[str] = field(default_factory=list)
    norm_relatives: list[tuple[str, str]] = field(default_factory=list)
    norm_phones: list[str] = field(default_factory=list)
    norm_emails: list[str] = field(default_factory=list)
    strong_ids: set[tuple[str, str]] = field(default_factory=set)
    dob_iso: str | None = None
    age_approx: int | None = None
    gender: str | None = None
    norm_village: str | None = None
    norm_pin: str | None = None


def _is_deva_letter(ch: str) -> bool:
    return "\u0900" <= ch <= "\u097f" and not ("\u0966" <= ch <= "\u096f")


def devanagari_to_latin(text: str) -> str:
    """Transliterate Unicode Devanagari text and digits to readable ASCII Latin."""
    if not text:
        return ""
    s = _ascii_digits(unicodedata.normalize("NFC", text))
    out: list[str] = []
    n = len(s)
    i = 0

    while i < n:
        ch = s[i]
        # Check two-character consonant + nukta sequences (Unicode NFC keeps them decomposed).
        pair = s[i : i + 2] if i + 1 < n and s[i + 1] == _NUKTA else ""
        cons = _DEVA_CONSONANTS.get(pair) if pair else _DEVA_CONSONANTS.get(ch)
        step = 2 if (pair and cons is not None) else 1

        if cons is not None:
            # Common Sanskrit/Hindi conjunct ज्ञ -> gy
            if (
                ch == "ज"
                and step == 1
                and i + 2 < n
                and s[i + 1] == _VIRAMA
                and s[i + 2] == "ञ"
            ):
                out.append("gy")
                i += 3
                if i < n and s[i] in _DEVA_MATRAS:
                    out.append(_DEVA_MATRAS[s[i]])
                    i += 1
                elif i < n and _is_deva_letter(s[i]):
                    out.append("a")
                continue

            out.append(cons)
            i += step
            if i < n and s[i] in _DEVA_MATRAS:
                out.append(_DEVA_MATRAS[s[i]])
                i += 1
            elif i < n and s[i] == _VIRAMA:
                i += 1
            elif i < n and s[i] in (_ANUSVARA, _CHANDRABINDU, _VISARGA):
                # Inherent 'a' precedes nasalization/visarga on bare consonants.
                out.append("a")
            else:
                # Word-final bare consonants drop inherent schwa; internal ones keep 'a'.
                next_is_word_char = i < n and (
                    s[i] in _DEVA_CONSONANTS
                    or s[i] in _DEVA_INDEPENDENT_VOWELS
                    or (i + 1 < n and s[i + 1] == _NUKTA)
                )
                if next_is_word_char:
                    out.append("a")
            continue

        if ch in _DEVA_INDEPENDENT_VOWELS:
            out.append(_DEVA_INDEPENDENT_VOWELS[ch])
            i += 1
            continue

        if ch == _ANUSVARA:
            next_ch = s[i + 1] if i + 1 < n else ""
            out.append("m" if next_ch in _DEVA_LABIALS else "n")
            i += 1
            continue

        if ch == _CHANDRABINDU:
            out.append("n")
            i += 1
            continue

        if ch == _VISARGA:
            out.append("h")
            i += 1
            continue

        if ch in (_NUKTA, _VIRAMA, "।"):
            out.append(" " if ch == "।" else "")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def normalize_name(raw: str) -> str:
    """Normalize EN/HI/Hinglish name to clean lowercase Latin without titles/honorifics."""
    if not raw:
        return ""
    latin = devanagari_to_latin(raw).lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", latin)
    raw_tokens = [t for t in cleaned.split() if t]
    if not raw_tokens:
        return ""

    # Map common Muslim/Indian prefix abbreviations (Md./Mohd./Muhammad -> mohammad).
    tokens = [_NAME_TOKEN_ALIASES.get(t, t) for t in raw_tokens]

    while tokens and tokens[0] in _LEADING_TITLES:
        tokens.pop(0)
    if len(tokens) > 1 and tokens[0] in _LEADING_OPTIONAL_TITLES:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in _TRAILING_HONORIFICS:
        tokens.pop()

    return " ".join(tokens)


def _token_phonetic_skeleton(token: str) -> str:
    """Reduce a normalized Latin name token to a cross-variant phonetic skeleton."""
    t = token.lower()
    if not t:
        return ""

    # Preserve leading vowel bucket (a/i/u) so Asha (as) != Usha (us) and Anita != Nita.
    lead_vowel = ""
    if t[0] in "aeiou":
        if t[0] in ("a", "e"):
            lead_vowel = "a"
        elif t[0] == "i":
            lead_vowel = "i"
        else:
            lead_vowel = "u"

    # Protect 'ch'/'chh' before mapping remaining 'c' -> 'k'.
    t = t.replace("ph", "f").replace("sh", "s")
    t = t.replace("chh", "\x01").replace("ch", "\x01")
    t = t.replace("kh", "k").replace("gh", "g").replace("bh", "b")
    t = t.replace("dh", "d").replace("th", "t").replace("jh", "j")
    t = t.replace("qu", "k").replace("q", "k").replace("w", "v").replace("x", "ks")
    t = t.replace("ck", "k").replace("c", "k").replace("\x01", "c")

    # Drop trailing vowel-glides and -ry/-iy endings ('pandey'/'pandeya'/'pande', 'vijay'/'vijai', 'tiwary'/'tiwari').
    if len(t) > 3 and t.endswith(("eya", "aya")):
        t = t[:-2]
    elif len(t) > 2 and t.endswith(("ey", "ay", "oy", "uy", "ry")):
        t = t[:-1]

    # Drop internal vowels and collapse repeated consonants.
    body = t[1:] if lead_vowel else t
    consonants = [ch for ch in body if ch not in "aeiou" and ch.isalpha()]
    deduped: list[str] = []
    for ch in consonants:
        if not deduped or deduped[-1] != ch:
            deduped.append(ch)
    if lead_vowel and deduped and deduped[0] == lead_vowel:
        return "".join(deduped)
    return lead_vowel + "".join(deduped)


def phonetic_skeleton(norm_name: str) -> str:
    """Build space-joined consonant/phonetic skeleton for a normalized name."""
    parts = [_token_phonetic_skeleton(tok) for tok in norm_name.split() if tok]
    return " ".join(p for p in parts if p)


def normalize_phone(raw: str) -> str | None:
    """Normalize Indian phone numbers to 10 ASCII digits starting with 6-9."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", _ascii_digits(raw))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None


def normalize_dob(raw: str | None, hospital_hint: str | None = None) -> str | None:
    """Parse DD/MM/YYYY, MM/DD/YYYY, or YYYY-MM-DD DOB strings into YYYY-MM-DD."""
    if not raw:
        return None
    s = _ascii_digits(raw).strip()
    if not s:
        return None

    m_iso = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            return dt.date(y, m, d).isoformat()
        except ValueError:
            return None

    m_num = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m_num:
        p1, p2, y = int(m_num.group(1)), int(m_num.group(2)), int(m_num.group(3))
        if not (1900 <= y <= 2030):
            return None
        # Arogya legacy/discharge exports use MM/DD/YYYY; other facilities use DD/MM/YYYY.
        order = ((p1, p2), (p2, p1)) if hospital_hint == "arogya" else ((p2, p1), (p1, p2))
        for month, day in order:
            try:
                return dt.date(y, month, day).isoformat()
            except ValueError:
                continue
    return None


def normalize_gender(raw: str | None) -> str | None:
    """Normalize English/Hindi gender strings into 'M', 'F', 'O', or None."""
    if not raw:
        return None
    return _parse_gender(raw)


def normalize_village(raw: str | None) -> str | None:
    """Normalize village/locality string into clean lowercase Latin text."""
    if not raw:
        return None
    low_raw = _ascii_digits(raw).strip().lower()
    if any(frag in low_raw for frag in _ADDR_LANDMARK_FRAGMENTS):
        return None
    latin = devanagari_to_latin(raw).lower()
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", latin)
    tokens = [t for t in cleaned.split() if t]
    while tokens and tokens[0] in ("vill", "village", "gram"):
        tokens.pop(0)
    joined = " ".join(tokens)
    if not joined or any(frag in joined for frag in ("ke pas", "ke piche", "ke samne")):
        return None
    return joined


def normalize_pin(raw: str | None) -> str | None:
    """Normalize 6-digit Indian PIN code."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", _ascii_digits(raw))
    return digits if len(digits) == 6 else None


def _normalize_strong_ids(rec: RecordIdentity) -> set[tuple[str, str]]:
    strong: set[tuple[str, str]] = set()
    for val in rec.abha:
        digits = re.sub(r"\D", "", _ascii_digits(val))
        if len(digits) == 14:
            strong.add(("abha", digits))

    for val in rec.aadhaar:
        if "x" in val.lower():
            # Masked Aadhaar (e.g. XXXX XXXX 5866) is not a unique strong ID.
            continue
        digits = re.sub(r"\D", "", _ascii_digits(val))
        if len(digits) == 12:
            strong.add(("aadhaar", digits))

    for val in rec.pan:
        cand = re.sub(r"\s+", "", _ascii_digits(val).upper())
        if _RE_PAN.fullmatch(cand):
            strong.add(("pan", cand))

    for val in rec.insurance_ids:
        cand = re.sub(r"[\s-]+", "", _ascii_digits(val).upper())
        if cand:
            strong.add(("insurance", cand))

    for val in rec.mrns:
        cand = re.sub(r"\s+", "", _ascii_digits(val).upper())
        if not cand:
            continue
        # Preserve hospital prefix (SNJ/26/..., JJH-..., etc.); scope bare numeric MRNs.
        if not re.search(r"[A-Z]", cand) and rec.hospital_hint:
            cand = f"{rec.hospital_hint.upper()}:{cand}"
        strong.add(("mrn", cand))

    return strong


def _normalize_relatives(relatives: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw_rel, raw_name in relatives:
        low_name = raw_name.strip().lower()
        if not low_name or any(w in low_name for w in _CLINICAL_TRAP_WORDS):
            continue
        rel_key = _REL_CANON_MAP.get(raw_rel.strip().lower(), raw_rel.strip())
        if rel_key.upper() == "K/C/O":
            continue
        norm_rname = normalize_name(raw_name)
        if not norm_rname:
            continue
        pair = (rel_key, norm_rname)
        if pair not in out:
            out.append(pair)
    return out


def normalize_identity(rec: RecordIdentity) -> NormalizedIdentity:
    """Canonicalize all extracted raw attributes of a RecordIdentity."""
    norm_names: list[str] = []
    name_tokens: list[list[str]] = []
    name_skeletons: list[str] = []

    for raw_name in rec.names:
        low = raw_name.strip().lower()
        if not low or any(w in low for w in _CLINICAL_TRAP_WORDS):
            continue
        nn = normalize_name(raw_name)
        if not nn:
            continue
        if nn not in norm_names:
            norm_names.append(nn)
            name_tokens.append(nn.split())
        skel = phonetic_skeleton(nn)
        if skel and skel not in name_skeletons:
            name_skeletons.append(skel)

    norm_phones: list[str] = []
    for p in rec.phones:
        np = normalize_phone(p)
        if np and np not in norm_phones:
            norm_phones.append(np)

    norm_emails: list[str] = []
    for em in rec.emails:
        ne = em.strip().lower()
        if ne and ne not in norm_emails:
            norm_emails.append(ne)

    return NormalizedIdentity(
        relpath=rec.relpath,
        loc=rec.loc,
        hospital_hint=rec.hospital_hint,
        norm_names=norm_names,
        name_tokens=name_tokens,
        name_skeletons=name_skeletons,
        norm_relatives=_normalize_relatives(rec.relatives),
        norm_phones=norm_phones,
        norm_emails=norm_emails,
        strong_ids=_normalize_strong_ids(rec),
        dob_iso=normalize_dob(rec.dob, rec.hospital_hint),
        age_approx=rec.age,
        gender=normalize_gender(rec.gender),
        norm_village=normalize_village(rec.village),
        norm_pin=normalize_pin(rec.pin),
    )


# ---------------------------------------------------------------------------
# Phase 3: Candidate Blocking, Conflict Vetoes, Scoring, and Clustering
# ---------------------------------------------------------------------------

LINK_THRESHOLD = 50.0
MEDIUM_THRESHOLD = 30.0
_MAX_PHONE_BUCKET_SIZE = 25
_MAX_NAME_BUCKET_SIZE = 150

_NATIONAL_ID_TYPES = ("abha", "aadhaar", "pan")
_STRONG_ANCHOR_TYPES = ("abha", "aadhaar", "pan", "insurance")
_STRICT_REL_TYPES = frozenset({"S/o", "D/o", "W/o", "C/o", "Father", "Mother"})


def _mrn_series(mrn: str) -> str:
    """Extract hospital MRN series prefix (e.g. 'SNJ/26/', 'IPD-2026-', 'JJH-')."""
    return re.sub(r"\d+$", "", mrn)


def _ids_by_type(norm: NormalizedIdentity) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for id_type, val in norm.strong_ids:
        grouped[id_type].add(val)
    return grouped


def _relative_names_compatible(name_a: str, name_b: str) -> bool:
    """Return True if two normalized relative names refer to the same person."""
    sa = phonetic_skeleton(name_a)
    sb = phonetic_skeleton(name_b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True
    ta = set(sa.split())
    tb = set(sb.split())
    if (len(ta) == 1 or len(tb) == 1) and bool(ta & tb):
        return True
    return difflib.SequenceMatcher(None, sa, sb).ratio() >= 0.75


def _has_compatible_relative_pair(
    rels_a: list[tuple[str, str]], rels_b: list[tuple[str, str]]
) -> bool:
    """Check if two records share at least one compatible relative name."""
    for _, name_a in rels_a:
        for _, name_b in rels_b:
            if _relative_names_compatible(name_a, name_b):
                return True
    return False


def generate_candidate_pairs(norms: list[NormalizedIdentity]) -> list[tuple[int, int]]:
    """Build inverted-index blocking buckets and return unique sorted index pairs."""
    phone_counts: Counter[str] = Counter()
    for norm in norms:
        for ph in set(norm.norm_phones):
            phone_counts[ph] += 1

    buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)

    for idx, norm in enumerate(norms):
        # 1. Strong ID buckets (ABHA, Aadhaar, PAN, Insurance, hospital-scoped MRN)
        for id_type, val in norm.strong_ids:
            buckets[("id", id_type, val)].append(idx)

        # 2. Phone bucket (skip shared hospital helplines appearing >25 times)
        for ph in norm.norm_phones:
            if phone_counts[ph] <= _MAX_PHONE_BUCKET_SIZE:
                buckets[("phone", ph)].append(idx)

        # 3. Name-skeleton buckets: (first_skel, last_skel) + partial (token_skel, pin/rel)
        rel_token_skels = {
            _token_phonetic_skeleton(rtok)
            for _, rname in norm.norm_relatives
            for rtok in rname.split()
        }
        rel_token_skels.discard("")

        for tokens in norm.name_tokens:
            if len(tokens) >= 2:
                first_s = _token_phonetic_skeleton(tokens[0])
                last_s = _token_phonetic_skeleton(tokens[-1])
                if first_s and last_s:
                    buckets[("fl", first_s, last_s)].append(idx)

            for tok in tokens:
                ts = _token_phonetic_skeleton(tok)
                if not ts:
                    continue
                if norm.norm_pin:
                    buckets[("tok_pin", ts, norm.norm_pin)].append(idx)
                for rts in rel_token_skels:
                    buckets[("tok_rel", ts, rts)].append(idx)

    pairs: set[tuple[int, int]] = set()
    for bkey, idxs in buckets.items():
        if len(idxs) < 2:
            continue
        if bkey[0] in ("fl", "tok_pin", "tok_rel") and len(idxs) > _MAX_NAME_BUCKET_SIZE:
            continue
        for pos, i in enumerate(idxs):
            for j in idxs[pos + 1 :]:
                if i < j:
                    pairs.add((i, j))
                elif j < i:
                    pairs.add((j, i))

    return sorted(pairs)


def has_hard_conflict(a: NormalizedIdentity, b: NormalizedIdentity) -> bool:
    """Return True if two records contain mutually incompatible identity evidence."""
    ids_a = _ids_by_type(a)
    ids_b = _ids_by_type(b)

    # 1. National ID mismatch (ABHA, Aadhaar, PAN)
    has_national_match = False
    for id_type in _NATIONAL_ID_TYPES:
        va, vb = ids_a.get(id_type), ids_b.get(id_type)
        if va and vb:
            if not (va & vb):
                return True
            has_national_match = True

    # 2. Gender mismatch
    if a.gender in ("M", "F") and b.gender in ("M", "F") and a.gender != b.gender:
        return True

    # 3. Exact DOB mismatch
    if a.dob_iso and b.dob_iso and a.dob_iso != b.dob_iso:
        return True

    # 4. Age difference > 3 years (unless exact DOB already matches)
    if a.age_approx is not None and b.age_approx is not None:
        if not (a.dob_iso and b.dob_iso and a.dob_iso == b.dob_iso):
            if abs(a.age_approx - b.age_approx) > 3:
                return True

    # 5. Relative name conflict for the same relation type
    rels_a: dict[str, list[str]] = defaultdict(list)
    rels_b: dict[str, list[str]] = defaultdict(list)
    for rel, rname in a.norm_relatives:
        if rel in _STRICT_REL_TYPES:
            rels_a[rel].append(rname)
    for rel, rname in b.norm_relatives:
        if rel in _STRICT_REL_TYPES:
            rels_b[rel].append(rname)
    for rel, names_a in rels_a.items():
        names_b = rels_b.get(rel)
        if names_b and not any(
            _relative_names_compatible(na, nb) for na in names_a for nb in names_b
        ):
            return True

    # 6. Same-hospital, same-series MRN mismatch (allow OPD vs IPD series, and allow
    # explicitly corroborated "old card merged" duplicates).
    if a.hospital_hint and b.hospital_hint and a.hospital_hint == b.hospital_hint:
        mrns_a = ids_a.get("mrn", set())
        mrns_b = ids_b.get("mrn", set())
        if mrns_a and mrns_b:
            series_a: dict[str, set[str]] = defaultdict(set)
            series_b: dict[str, set[str]] = defaultdict(set)
            for m in mrns_a:
                series_a[_mrn_series(m)].add(m)
            for m in mrns_b:
                series_b[_mrn_series(m)].add(m)

            common_series = set(series_a) & set(series_b)
            for series in common_series:
                if not (series_a[series] & series_b[series]):
                    same_triple = (
                        bool(a.dob_iso and b.dob_iso and a.dob_iso == b.dob_iso)
                        and bool(set(a.norm_phones) & set(b.norm_phones))
                        and _has_compatible_relative_pair(a.norm_relatives, b.norm_relatives)
                    )
                    if not (has_national_match or same_triple):
                        return True

    return False


def _name_match_score(a: NormalizedIdentity, b: NormalizedIdentity) -> float:
    """Compute name similarity points (+25 full match, +12 partial/single-token, else 0)."""
    if not a.norm_names or not b.norm_names:
        return 0.0

    full_skels_a = {s for s in a.name_skeletons if " " in s}
    full_skels_b = {s for s in b.name_skeletons if " " in s}
    if full_skels_a & full_skels_b:
        return 25.0

    # Check multi-token similarity >= 0.85 across normalized names / skeleton token sets
    multi_a = [t for t in a.name_tokens if len(t) >= 2]
    multi_b = [t for t in b.name_tokens if len(t) >= 2]
    for ta in multi_a:
        sa_toks = {_token_phonetic_skeleton(t) for t in ta}
        sa_str = " ".join(_token_phonetic_skeleton(t) for t in ta)
        for tb in multi_b:
            sb_toks = {_token_phonetic_skeleton(t) for t in tb}
            sb_str = " ".join(_token_phonetic_skeleton(t) for t in tb)
            if sa_toks == sb_toks:
                return 25.0
            if difflib.SequenceMatcher(None, sa_str, sb_str).ratio() >= 0.85:
                return 25.0

    # Partial / single-token name match (cannot cross LINK_THRESHOLD alone)
    tok_skels_a = {_token_phonetic_skeleton(t) for toks in a.name_tokens for t in toks}
    tok_skels_b = {_token_phonetic_skeleton(t) for toks in b.name_tokens for t in toks}
    tok_skels_a.discard("")
    tok_skels_b.discard("")
    if tok_skels_a & tok_skels_b:
        return 12.0

    return 0.0


def score_pair(a: NormalizedIdentity, b: NormalizedIdentity) -> float:
    """Score identity compatibility between two normalized records (-inf on hard conflict)."""
    if has_hard_conflict(a, b):
        return float("-inf")

    score = 0.0
    ids_a = _ids_by_type(a)
    ids_b = _ids_by_type(b)

    # +50: exact ABHA, Aadhaar, PAN, or Insurance ID match
    if any(ids_a.get(t, set()) & ids_b.get(t, set()) for t in _STRONG_ANCHOR_TYPES):
        score += 50.0

    # +45: exact hospital-scoped MRN match
    if ids_a.get("mrn", set()) & ids_b.get("mrn", set()):
        score += 45.0

    # +30: exact 10-digit phone match
    if set(a.norm_phones) & set(b.norm_phones):
        score += 30.0

    # +25 full name skeleton / >=0.85 token match, or +12 partial/single-token
    score += _name_match_score(a, b)

    # +15: compatible relative / guardian skeleton match
    if _has_compatible_relative_pair(a.norm_relatives, b.norm_relatives):
        score += 15.0

    # +10 exact DOB match, or +5 age within <= 2 years
    if a.dob_iso and b.dob_iso and a.dob_iso == b.dob_iso:
        score += 10.0
    elif a.age_approx is not None and b.age_approx is not None:
        if abs(a.age_approx - b.age_approx) <= 2:
            score += 5.0

    # +8: same PIN or normalized village phonetic skeleton
    same_pin = bool(a.norm_pin and b.norm_pin and a.norm_pin == b.norm_pin)
    same_village = bool(
        a.norm_village
        and b.norm_village
        and phonetic_skeleton(a.norm_village) == phonetic_skeleton(b.norm_village)
    )
    if same_pin or same_village:
        score += 8.0

    return score


_KEY_PRIORITY_ORDER = ("abha", "aadhaar", "pan", "insurance", "mrn")


def _build_cluster_key(cluster_norms: list[NormalizedIdentity]) -> str:
    """Build deterministic, secret-independent canonical patient_key for a cluster."""
    by_type: dict[str, list[str]] = defaultdict(list)
    phones: list[str] = []
    demogs: list[tuple[str, str, str]] = []

    for norm in cluster_norms:
        for id_type, val in norm.strong_ids:
            by_type[id_type].append(val)
        phones.extend(norm.norm_phones)
        if norm.norm_names and norm.dob_iso and norm.gender:
            demogs.append((min(norm.norm_names), norm.dob_iso, norm.gender))

    for id_type in _KEY_PRIORITY_ORDER:
        vals = by_type.get(id_type)
        if vals:
            return f"{id_type}:{min(vals)}"

    if phones:
        return f"phone:{min(phones)}"

    if demogs:
        name, dob, gender = min(demogs)
        return f"demog:{name}|{dob}|{gender}"

    min_rel, min_loc = min((n.relpath, n.loc) for n in cluster_norms)
    return f"rec:{min_rel}|{min_loc}"
