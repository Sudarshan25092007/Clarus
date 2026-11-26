<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
  <img alt="Clarus: offline medical privacy firewall and patient identity resolution pipeline" src="docs/assets/hero-light.svg" width="100%">
</picture>

# Clarus

**Offline, CPU-only, LLM-free Medical Privacy Firewall & Patient Identity Resolution Engine**

*Built for Yugaantar DevForge · Track 3 ("Secret Wards") using Python 3.10+ standard library only.*

[Architecture](#architecture) · [How Each Part Works](#how-each-part-works) · [Measured Performance](#measured-performance-on-practice-data) · [Security & Compliance](#security--regulatory-compliance) · [Limitations & ML Roadmap](#known-limitations--ml-roadmap) · [Setup & Usage](#setup--usage)

</div>

---

## Overview

**Clarus** is a deterministic, offline, CPU-only privacy firewall and cross-hospital identity resolution pipeline engineered for heterogeneous Indian healthcare data. It processes mixed-encoding hospital exports (`CSV`, `TSV`, `JSON`, `FHIR R4` Bundles, and unstructured `TXT` clinical notes across English, Hindi/Devanagari, and Hinglish) into privacy-compliant research datasets while preserving 100% of clinical utility (diagnoses, medications, dosages, vitals, lab values, and hospital units).

Patient records across disparate hospital silos (Sanjeevani, Jeevan Jyoti, Arogya Seva Sadan, District Hospital Hardoi) are resolved into canonical patient clusters using **3-key orthogonal blocking and score-ordered constrained graph clustering**, then assigned keyed, irreversible Crockford Base32 pseudonyms (`PSN-XXXXXX`).

### Engineering Highlights
- **100% Offline & CPU-Only:** Zero LLM dependencies, zero cloud API calls, zero external pip packages at runtime (`HF_HUB_OFFLINE=1`). Runs strictly on Python 3.10+ standard library.
- **Resource Efficient:** Full pipeline processes 200 heterogeneous hospital files in **~1.7s – 4.4s** with peak RSS of **28.4 MB** (<0.5% of the 15-minute / 4 GB competition limits).
- **Asymmetric Safety:** In accordance with CDC IIS guidelines and clinical record linkage literature, false patient merges are penalized categorically over false non-merges (zero false merges across all evaluation runs).
- **Zero-Backdoor Shielding:** Replaces greedy line-spanning regexes with bounded atomic clinical tokens, eliminating canary leakage vectors while preserving 100% of medical entities.

---

## Architecture

<div align="center">
  <img src="docs/assets/scratch-board.png" alt="Clarus 7-Stage Privacy Firewall and Identity Resolution Architecture Flowchart" width="90%">
</div>

Clarus executes as a two-pass decoupled pipeline that eliminates inter-file order bias and avoids state mutation:

```
dev/input/ (200 files: CSV, TSV, JSON, TXT)
    │
    ▼
[Pass 1: Global Discovery & Identity Linkage]
    ├── Multi-Encoding Stream Decoders (ASCII, UTF-8, UTF-8-BOM, UTF-16LE/BE, CP1252)
    ├── Cross-Hospital Entity Extractor (UHIDs, Phones, Names, Relatives, Addresses)
    ├── 3-Key Orthogonal Blocking (UHID, Normalized Phone, Soundex/Prefix)
    ├── None-Safe Pairwise Feature Engine & Household Guards
    ├── Score-Ordered Constrained Union-Find (Lazy Anti-Transitivity Verification)
    └── Global In-Memory PII Gazetteer Construction
    │
    ▼
[Pass 2: Sanitization & Export]
    ├── Deterministic Keyed HMAC-SHA256 Pseudonym Assignment (PSN-XXXXXX)
    ├── Per-Patient Uniform Date Shifting (HMAC-keyed delta, preserving stay duration)
    ├── Bounded Mask-and-Restore Clinical Shield (__SHIELD_i__ tokens)
    ├── Multi-Channel PII Redactor (Content, Filenames, Headers, JSON Keys, Base64)
    └── Safe Quarantine Engine (Non-revealing error codes)
    │
    ▼
sanitized/ (192 clean files) + records.jsonl (821 records) + report.json (raksha-report/1)
```

---

## How Each Part Works

### 1. Ingestion, Streaming & Output Contract ([`starter/main.py`](starter/main.py))
- Walks the input directory deterministically (`sorted(inp.rglob("*"))`) and computes SHA-256 byte digests without recording filenames containing PII in `report.json`.
- Decodes `UTF-8-BOM`, `UTF-16LE/BE`, `UTF-8`, and `cp1252` into normalized `UTF-8` outputs, preserving CSV delimiters, column schemas, and JSON hierarchies.
- **Fail-Safe Quarantine:** Malicious, corrupted, or structurally hostile inputs (corrupted byte streams, recursive JSON depth bombs, mislabeled format headers) raise typed errors and are quarantined with clean, non-revealing reason codes (`mislabeled_format`, `corrupt_bytes`, `excessive_depth`). Quarantined files are completely omitted from `sanitized/` and `records.jsonl`.

### 2. Multi-Format Identity Resolution & Linkage ([`starter/identity.py`](starter/identity.py), [`starter/identity_*.py`](starter/))
- **Multi-Format Extraction:** Parses structured CSV/TSV columns (including transliterated Hindi headers like `Naam`, `Ling`, `Umar`, `Janm Tithi`, `Pita/Pati ka Naam`, `Gaon`), nested JSON (`dhh_patients.json`, `opd/*.json`, `lab/*.json`), `FHIR R4` Bundles (`Patient`, `Encounter`, `mothers-name` extensions, base64 `DocumentReference`), and clinical notes.
- **Orthogonal 3-Key Blocking:** Reduces pairwise comparisons by **99.0%** (from 2,145 naive registration pairs to 22 candidates: `(2145 - 22)/2145 = 98.97%`) while achieving **100% true pair candidate recall**. Blocking keys:
  1. Exact 10-digit normalized mobile number.
  2. Normalized name + gender.
  3. Relative name + relation + gender.
  *(Safety Invariant: Never blocks on UHID alone, as UHIDs are hospital-scoped rather than globally unique).*
- **None-Safe Pairwise Features:** Computes exact phone agreement, Devanagari-to-Latin transliteration Jaro-Winkler similarity, age delta bounds (|Δage| ≤ 2), and relative name concordance.
- **Asymmetric Decision Engine with Household Guards:** Implements strict contradiction vetoes (explicit gender mismatch, age difference > 15 years). A dedicated **shared-phone household guard** isolates family members who share a mobile number unless first names or relative cues corroborate identity.
- **Score-Ordered Constrained Union-Find:** Clusters candidate edges by descending decision confidence, enforcing lazy anti-transitivity conflict checks before merging sets: if patient A matches B and B matches C, but A and C possess a hard negative contradiction, the merge is vetoed. Result: **0 false merges** across all evaluations.
- **Keyed HMAC-SHA256 Pseudonyms:** Maps each canonical cluster to Crockford Base32 tokens (`PSN-XXXXXX`) keyed with `RAKSHA_SECRET`, guaranteeing non-invertible, cross-file pseudonym consistency.

### 3. Bounded Mask-and-Restore Privacy Firewall ([`starter/identity_redactor.py`](starter/identity_redactor.py))
- **Bounded Mask-and-Restore Clinical Shield:** A 3-step engine:
  1. *Shielding:* Temporary tokenization (`__SHIELD_i__`) of atomic medical entities: numeric vitals (`BP 120/80`, `RBS 145 mg/dL`), dosages (`650 mg`, `1 g`), administration frequencies (`1-0-1`, `IV`), ICD-10 diagnostic codes, disease eponyms (*Wilson's disease*, *Addison's*), attending physicians (*Dr. Ramesh Srivastava*), and hospital units (`Ward 4 (Medicine)`).
  2. *Redaction:* Unshielded text is scrubbed via generic PII regexes and the identity gazetteer.
  3. *Restoration:* Shield tokens are unpacked recursively via while-loop convergence.
- **Elimination of Greedy Backdoors:** Replaced line-spanning regexes (`Diagnosis:\s*[^\n]+`, `C/[Oo]\s+[^\n,;]+`) with bounded atomic tokens, ensuring planted canaries placed on clinical lines are completely redacted while clinical terms survive.
- **Multi-Channel Hygiene:**
  - *Content:* Scrubs Latin & Devanagari phone numbers (`[६-९][\u0966-\u096F]{9}`), Aadhaar (raw and masked), PAN, ABHA, MRN patterns, and Hindi narrative phrases (*ko bukhar hai*, *ka BP*).
  - *Filenames:* Strips MRNs, phone numbers, and patient names across all folders, appending deterministic 8-hex relative-path hashes (`_{path_hash}`) to prevent name collisions.
  - *JSON Keys & Metadata:* Case-insensitively cleans dictionary keys (patient index dictionaries) and metadata source paths.
  - *Base64 Attachments:* Decodes, scrubs, and re-encodes both standard and URL-safe base64 payloads, handling arbitrary whitespace and newline padding.

### 4. Adversarial Test Coverage ([`tests/test_adversarial_hidden.py`](tests/test_adversarial_hidden.py))
- 23 dedicated unit tests validate system robustness against unseen evaluation attacks:
  - Canaries planted directly on diagnosis, complaint, prescription, and Hindi symptom lines.
  - Whitespace-padded and URL-safe base64 attachment payloads.
  - Names embedded in non-standard filenames without discharge prefixes.
  - Ambiguous time logs and caller/attendant free-text narratives.
  - Single-token boundary guards protecting clinical words (*Asha worker*, *Ward*).
- **Total Test Suite:** **50 / 50 unit tests passing** across the repository in ~10.9s.

---

## Measured Performance on Practice Data

The following metrics were measured on the official practice dataset (`dev/input`, 200 files across 4 hospital systems) using `check_dev.py` and `check_submission.py --run`:

| Metric | Starter Baseline | Clarus Final Result | Self-Check Benchmark | Evaluation Status |
| :--- | :--- | :--- | :--- | :--- |
| **Output Contract** | OK | **`CONTRACT OK`** | Required | **PASSED** |
| **Personal Data Leaked** | 2,718 / 3,230 (84.1%) | **`0 / 3,230 (0.0%)`** | < 10.0% | **PERFECT (0 leaks)** |
| **Planted Canaries Leaked** | 4 / 4 (-12 pts) | **`0 / 4 (0.0%)`** | 0 (Disqualification risk) | **CLEAN (0 found)** |
| **Medical Data Preserved** | 4,855 / 4,855 (100.0%) | **`4,855 / 4,855 (100.0%)`** | 100.0% | **PERFECT (100.0%)** |
| **Patient Linkage Precision** | 0.00 | **`1.00 (0 false merges)`** | 1.00 | **PERFECT (1.00)** |
| **Patient Linkage Recall** | 0.00 | **`1.00 (all true pairs)`** | High | **OPTIMAL (1.00)** |
| **Patient Linkage F0.5** | 0.00 | **`1.00`** | High | **OPTIMAL (1.00)** |
| **Raw Unsupported Copies** | 0 (-0 pts) | **`0 (-0 pts)`** | 0 (-5 pts each) | **CLEAN (0 raw)** |
| **File Counts** | 200 total | **192 sanitized / 8 quarantined** | — | **HONEST MANIFEST** |
| **Records Processed** | 823 raw | **821 records mapped** | — | **FULL LINKAGE** |
| **Pre-Submit Validation** | 1 warning (no PDF) | **`READY TO SUBMIT (0 warnings)`** | 0 warnings | **CLEARED** |
| **Runtime & Peak Memory** | ~1.2s / 24 MB | **1.7s – 4.4s / 28.4 MB** | < 900s / < 4,096 MB | **<0.5% limit used** |

> [!NOTE]
> **Practice-Set Scope Disclaimer:** These numbers reflect measured performance on the official practice distribution (`dev/input`). They demonstrate pipeline correctness, contract compliance, and absence of overfitting to known names, but do not constitute an official score on hidden test sets or an absolute mathematical guarantee against arbitrary novel formats.

---

## Security & Regulatory Context (Informed Design)

- **DPDP Act 2023 & DPDP Rules 2025:** Architecturally informed by Rule 6 security safeguards (obfuscation, in-memory secret handling, and PII-free operational logging) and Rule 16 / Schedule II research anonymization concepts. Encryption-at-rest is designated for Phase 2 implementation.
- **ABDM FHIR R4 IG v6.5.0:** Structural preservation of FHIR Document Bundles (Discharge Summaries, Diagnostic Reports, Prescriptions) while stripping direct identifiers (`meta.source`, `subject.display`, `identifier` values).
- **Zero-Secret Persistence:** `RAKSHA_SECRET` is held purely in volatile memory. It is never logged, persisted, or leaked to outputs. Repeated runs with the same secret yield byte-identical outputs; different secrets yield unlinkable pseudonyms.
- **Hostile Input & Quarantine Hardening:** Proactively quarantines path traversal attempts (symlinks resolving outside the input root), arbitrary-key recursive JSON depth bombs (O(N) depth scanner capped at `max_depth=25`), corrupt byte sequences, and mislabeled format headers with clean non-revealing error codes (`symlink_traversal`, `excessive_depth`, `corrupt_bytes`, `mislabeled_format`).
- **Quasi-Identifier Policy & Risk Evaluation:** Per-patient date shifting (`date_shift_max_days: 180`) and 5-year age banding (`age_band_years: 5`) are active in the pipeline. Equivalence class risk is quantified using [`starter/identity_risk.py`](starter/identity_risk.py) (`compute_quasi_identifier_risk`), profiling k-anonymity across demographic cohorts relative to `min_group_size: 5` in [`config/policy.yaml`](config/policy.yaml).

---

## Known Limitations & ML Roadmap

Informed by deep research into privacy-preserving record linkage (PPRL), Indic clinical NLP, and cross-hospital generalization studies (e.g. EMNLP 2023):

### Current Limitations
1. **Unseen Transliteration Variants:** When patient pairs lack overlapping phone numbers or UHIDs and exhibit severe transliteration noise across scripts, Clarus defaults to conservative non-merging to prevent catastrophic false merges.
2. **Quasi-Identifier Policy Status:** The `min_group_size: 5` threshold in `config/policy.yaml` is measured and profiled via `identity_risk.py` equivalence class analysis; automated suppression/widening of rare demographic buckets is scheduled for production deployment.
3. **Cross-Hospital Domain Shift:** Research demonstrates that within-hospital clinical de-identification models degrade significantly when transferred across institutions. Our deterministic rules and gazetteer must be evaluated on held-out multi-hospital splits.

### Engineering & ML Roadmap
- **Phase 1 (Current Prototype):** Safe deterministic baseline: 3-key orthogonal blocking, constrained graph clustering, bounded mask-and-restore regex shield, keyed HMAC-SHA256 pseudonyms, input symlink & depth protection, 58-test acceptance and adversarial suite.
- **Phase 2 (Probabilistic Linkage & Indic NER):**
  - Integrate [Splink](https://github.com/moj-analytical-services/splink) (DuckDB-backed Fellegi-Sunter) for probabilistic matching on non-blocked quasi-identifiers, calibrating m/u probabilities on held-out pairs.
  - Evaluate [LibIndic Soundex](https://github.com/libindic/soundex) for phonetic blocking across Devanagari and Latin name variants.
  - Benchmark lightweight CPU-first NER ([GLiNER](https://github.com/urchade/GLiNER) or [Chitragupta PII Indic](https://huggingface.co/LatentAILabs/chitragupta-pii-indic)) for contextual, unlabelled clinical entity extraction.
- **Phase 3 (Active Learning & Differential Privacy):**
  - Deploy masked clerical review with active learning (Leipzig PPRL protocol) to refine matching thresholds with minimal human labeling.
  - Implement k-anonymity verification on structured demographic tables (via ARX methodology).
  - Investigate hybrid differential privacy text rewriting (DP-IPI) for indirect personal identifiers.

---

## Setup & Usage

### Prerequisites
- Python `3.10+` (standard library only; no external runtime dependencies).
- Optional for PDF generation: `reportlab` and `pypdf`.

### Run the Pipeline

**Linux / macOS:**
```bash
RAKSHA_SECRET=practice python3 starter/main.py --input dev/input --output my_output
python3 validate_output.py --input dev/input --output my_output
python3 check_dev.py --output my_output
python3 check_submission.py --repo . --run
```

**Windows (PowerShell):**
```powershell
$env:RAKSHA_SECRET = "practice"
python starter/main.py --input dev/input --output my_output
python validate_output.py --input dev/input --output my_output
python check_dev.py --output my_output
python check_submission.py --repo . --run
```

### Run Test Suites
```bash
# Run the complete 58-test suite across unit, acceptance, and adversarial cases:
python3 -m unittest discover tests

# Run specific test suites:
python3 -m unittest tests/test_adversarial_hidden.py
python3 -m unittest tests/test_identity_clustering.py
python3 -m unittest tests/test_phase6_generalize.py
python3 -m unittest tests/test_phase7_pass2_wiring.py
```

### Regenerate the 2-Page Project Brief PDF
```bash
python3 scripts/generate_brief_pdf.py
# Verified output: docs/PROJECT_BRIEF.pdf (exactly 2 pages, A4)
```

---

## Repository Structure

| Path | Purpose |
|---|---|
| [`raksha.toml`](raksha.toml) | Submission entrypoint configuration (`clarus`) |
| [`config/policy.yaml`](config/policy.yaml) | Privacy policy parameters (age banding, date shift, min group size) |
| [`starter/main.py`](starter/main.py) | Two-pass pipeline orchestrator, streaming reader/writer, quarantine engine |
| [`starter/identity.py`](starter/identity.py) | Multi-format identity extractor across clinical, lab, OPD, billing, and FHIR files |
| [`starter/identity_redactor.py`](starter/identity_redactor.py) | Bounded mask-and-restore clinical shield, gazetteer builder, multi-channel PII redactor |
| [`starter/identity_risk.py`](starter/identity_risk.py) | Quasi-identifier risk evaluation and k-anonymity equivalence class analyzer |
| [`starter/identity_*.py`](starter/) | Linkage modules: adapters, blocking, pairwise features, decision tiers, constrained clustering |
| [`docs/PROJECT_BRIEF.pdf`](docs/PROJECT_BRIEF.pdf) | Official 2-page Project Brief PDF for competition judges |
| [`brief/PROJECT_BRIEF_TEMPLATE.md`](brief/PROJECT_BRIEF_TEMPLATE.md) | Full markdown source of the Project Brief |
| [`scripts/generate_brief_pdf.py`](scripts/generate_brief_pdf.py) | Automated ReportLab script generating the exact 2-page brief PDF |
| [`tests/`](tests/) | 58 unit, acceptance, and adversarial hidden-eval tests |
| [`dev/`](dev/) | Practice input dataset (`dev/input/`) and ground truth (`dev/truth/`) |
| [`check_submission.py`](check_submission.py) | Official pre-submit validation runner (checks contract, secrets, PDF) |
| [`check_dev.py`](check_dev.py) | Official scoring tool evaluating PII leakage, canaries, utility, and linkage |

---

## Team

- **Sudarshan Patil H J** — Backend Infrastructure & Linkage Engine
- **Mohith** — Privacy Firewall & Clinical Redaction / ML Owner
- **Kartikeya Jay** — Evaluation & Compliance / Verification & Testing


