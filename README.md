<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="Clarus: offline medical privacy firewall and patient identity resolution pipeline" src="docs/assets/hero-light.svg" width="100%">
</picture>

# Clarus

**Offline, CPU-only, LLM-free Medical Privacy Firewall & Patient Identity Resolution Engine**

*Built for Yugaantar DevForge · Track 3 ("Secret Wards") using Python 3.10+ standard library only.*

[Architecture](#architecture) · [How Each Part Works](#how-each-part-works) · [Setup & Usage](#setup--usage) · [Security Design](#security-design) · [Known Limitations](#known-limitations)

</div>

---

## Overview

**Clarus** sanitizes heterogeneous Indian hospital exports (`CSV`, `TSV`, `JSON`, `FHIR R4` Bundles, and unstructured `TXT` clinical notes across English, Hindi/Devanagari, and Hinglish) into a privacy-preserving research dataset while preserving clinical utility (diagnoses, medications, doses, vitals, and lab values) and linking patient records across files using deterministic, keyed `PSN-XXXXXX` pseudonyms.

Everything runs **100% offline on CPU** using only the **Python 3.10+ standard library** (zero LLMs, zero network calls, zero external packages).

---

## Architecture

<div align="center">
  <img src="docs/assets/scratch-board.png" alt="Clarus 7-Stage Privacy Firewall and Identity Resolution Architecture Flowchart" width="90%">
</div>

Clarus is designed as a deterministic seven-stage pipeline:

1. **Input Layer:** Ingests mixed-encoding exports (`UTF-8`, `UTF-8-BOM`, `UTF-16`, `cp1252`) across `CSV`, `TSV`, `JSON`, `FHIR R4`, and `TXT`.
2. **Safe Ingestion & Validation:** Traverses files in sorted order, enforces recursion-safe JSON parsing, and quarantines unsupported, empty, undecodable, or malformed files instead of copying them raw.
3. **Multilingual PII Detection:** Identifies and scrubs sensitive identifiers across free text, structured columns, JSON keys, metadata, filenames, and encoded attachments while protecting medical terms and dosages.
4. **Patient Identity Resolution:** Extracts raw identity signals, blocks candidate pairs, evaluates pairwise compatibility with hard-conflict vetoes, and clusters records via conflict-safe Union-Find.
5. **Privacy & Pseudonymization Layer:** Assigns keyed HMAC-SHA256 `PSN-XXXXXX` pseudonyms and applies policy-driven quasi-identifier protections (per-patient date shifting, age banding, location generalization, and rare-group suppression).
6. **Security & Output Validation:** Ensures zero secret/PII leakage in outputs or logs and validates output contract compliance.
7. **Required Outputs:** Emits `sanitized/`, `report.json` (SHA-256 input manifests and counts), and `records.jsonl` (record-to-pseudonym mappings).

---

## How Each Part Works

### 1. Ingestion, Sanitization & Output Contract ([`starter/main.py`](starter/main.py))
- Walks the input directory deterministically (`sorted(inp.rglob("*"))`) and computes SHA-256 digests of raw input bytes without recording filenames containing PII in `report.json`.
- Decodes `UTF-8-BOM`, `UTF-16`, `UTF-8`, and `cp1252` into normalized `UTF-8` outputs, preserving CSV delimiters/structure and JSON schemas.
- Quarantines unsupported formats (`.xml`, `.pdf`, `.jpg`), empty files, undecodable byte streams, and broken/deeply nested JSONs (`RecursionError`, `JSONDecodeError`), filtering quarantined files out of `records.jsonl`.
- Runs baseline regex redaction (`PHONE`, `AADHAAR`, `EMAIL`, `PAN`, `NAME`) recursively across text, CSV cells, and JSON values/keys.

### 2. Multi-Format Raw Identity Extraction ([`starter/identity.py`](starter/identity.py))
- Defines the `RecordIdentity` dataclass and `extract_identity()` to parse structured CSV/TSV columns (including transliterated Hindi headers such as `Naam`, `Ling`, `Umar`, `Janm Tithi`, `Pita/Pati ka Naam`, `Gaon`, `Aadhar`), nested JSON (`dhh_patients.json`, `opd/*.json`, `lab/*.json`), `FHIR R4` Bundles (`Patient`, `Encounter.identifier`, `contact`, `mothers-name` extensions, `DocumentReference` base64 text), and English/Hindi/Hinglish clinical notes (including Devanagari digits).
- Provides the `IdentityResolver` interface (`add_record`, `resolve_all`, `get_patient_id`, `get_patient_key`) for full cross-folder integration.

### 3. Registration Linkage Pipeline ([`starter/identity_*.py`](starter/))
- **Extraction & Adapters ([`identity_extractor.py`](starter/identity_extractor.py), [`identity_adapters.py`](starter/identity_adapters.py)):** Normalizes names, ages, genders, 10-digit phones, and relative names/relations across Sanjeevani CSV, Jeevanjyoti TSV, District Hospital Hardoi JSON, and Arogya legacy CSV.
- **Candidate Blocking ([`identity_linker.py`](starter/identity_linker.py)):** Generates deduplicated candidate pairs across 3 non-UHID blocking keys (`phone`, `name + gender`, `relative_name + relation + gender`), reducing `2,145` registration pairs to `22` (`99.0%` reduction with `100%` candidate recall on registration ground truth).
- **Pairwise Features & Decisioning ([`identity_features.py`](starter/identity_features.py), [`identity_decision.py`](starter/identity_decision.py)):** Computes `None`-safe similarity/conflict features and assigns `MATCH`, `NON_MATCH`, or `UNCERTAIN`, enforcing hard vetoes on gender conflicts, shared-phone age gaps (`> 5` years), and guardian contradictions.
- **Constrained Clustering & Pseudonyms ([`identity_clustering.py`](starter/identity_clustering.py), [`identity_pseudonym.py`](starter/identity_pseudonym.py), [`identity_pipeline.py`](starter/identity_pipeline.py)):** Executes score-ordered Constrained Union-Find with lazy cross-pair contradiction checks (partitioning `66` registration records into `52` clusters with `0` false merges), then computes deterministic HMAC-SHA256 `PSN-XXXXXX` IDs via `build_pseudonym_map()`.

---

## Setup & Usage

### Prerequisites
- Python `3.10+` (standard library only; no external `pip` dependencies required).

### Run the Pipeline

**Linux / macOS:**
```bash
RAKSHA_SECRET=practice python3 starter/main.py --input dev/input --output my_output
python3 validate_output.py --input dev/input --output my_output
python3 check_dev.py --output my_output
python3 check_submission.py --repo .
```

**Windows (PowerShell):**
```powershell
$env:RAKSHA_SECRET = "practice"
python starter/main.py --input dev/input --output my_output
python validate_output.py --input dev/input --output my_output
python check_dev.py --output my_output
python check_submission.py --repo .
```

### Run Test Suites & Static Checks
```bash
python tests/test_phase1_extract.py
python tests/test_identity_extractor.py
python tests/test_identity_linker.py
python tests/test_identity_features.py
python tests/test_identity_decision.py
python tests/test_identity_clustering.py
pyrefly check
```

---

## Security Design

- **Keyed HMAC Pseudonymization:** `RAKSHA_SECRET` is read from the environment and required at startup (the pipeline fails fast if missing or empty). Both `identity_pseudonym.py` and `main.py` compute `PSN-XXXXXX` tokens via HMAC-SHA256 so repeated runs with the same secret are byte-deterministic while different secrets produce unlinkable pseudonyms.
- **Zero Secret & Zero PII Leakage:** `RAKSHA_SECRET` is never written to outputs, `report.json`, `records.jsonl`, or stdout/stderr; stdout prints summary counts only, and `report.json` identifies inputs strictly by SHA-256 byte digests.
- **Fail-Safe Quarantine:** Unreadable, unsupported, empty, or malformed inputs raise typed errors before export and are excluded from `sanitized/` and `records.jsonl` with sanitized reason codes only.
- **Policy-Controlled Generalization:** Quasi-identifier policy rules (`age_band_years: 5`, `date_shift_max_days: 180`, `min_group_size: 5`) are centralized in [`config/policy.yaml`](config/policy.yaml) and [`starter/config/policy.yaml`](starter/config/policy.yaml).

---

## Baseline Metrics & Known Limitations

Measured on the practice dataset (`dev/input`, `200` files / `823` records) with `check_dev.py`:
- **Contract & Quarantine:** `CONTRACT OK`, `195` files sanitized, `5` files quarantined, `0` raw broken files copied.
- **Clinical Utility:** `100.0%` (`4855 / 4855`) medical values preserved.
- **Linkage Baseline:** `1.00` precision (`0` false patient merges), `0.01` recall (`F0.5 = 0.04`).

### Known Limitations (Active Roadmap)
- **PII Detection Coverage:** `starter/main.py` still uses baseline regex redaction (`84.1%` PII visible, `4/4` canaries remaining); contextual EN/HI/Hinglish entity detection, address/relative scrubbing, filename renaming, header sanitization, and base64 payload scrubbing remain to be integrated.
- **Cross-Folder & Cross-Script Linkage:** `identity_pipeline.py` currently links `registration/` records only (`66` rows). Wiring `starter/identity.py`'s multi-format extractor (`opd/`, `lab/`, `discharge/`, `notes/`, `prescriptions/`, `fhir/`, `billing/`) and adding Devanagari-to-Latin transliteration for cross-script matching are pending.
- **Temporal & Quasi-Identifier Generalization:** Per-patient `shift_date()`, `age_band()`, location generalization, and `min_group_size` suppression in `main.py` are not yet wired into CSV/JSON/text writers.
- **Submission Brief:** The 2-page Project Brief PDF (`docs/brief.pdf`) has not been generated yet.

---

## Repository Structure

| Path | Purpose |
|---|---|
| [`raksha.toml`](raksha.toml) | Submission entrypoint configuration (`clarus`) |
| [`config/policy.yaml`](config/policy.yaml) | Privacy generalization & shifting parameters |
| [`pyrefly.toml`](pyrefly.toml) | Static type-checking configuration (`search-path = [".", "starter"]`) |
| [`starter/`](starter/) | Core pipeline (`main.py`), multi-format identity extractor (`identity.py`), and linkage modules (`identity_*.py`) |
| [`tests/`](tests/) | Extraction, blocking, feature, decision, and clustering unit/acceptance test suites |
| [`validate_output.py`](validate_output.py), [`check_dev.py`](check_dev.py), [`check_submission.py`](check_submission.py) | Official contract, dev-score, and pre-submit validation scripts |
| [`dev/`](dev/) | Practice input dataset (`dev/input/`), ground truth (`dev/truth/`), and simulated attacker files (`dev/attacker/`) |
| [`docs/`](docs/) | Official specification, scoring, attack model, and media assets |
