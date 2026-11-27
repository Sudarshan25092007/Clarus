# Clarus: Deterministic Multi-Script Identity Resolution & Zero-Leakage Privacy Firewall

**Track 3 · Secret Wards** · Team: Sudarshan Patil H J (Backend Infrastructure & Linkage Engine), Mohit G R (Privacy Firewall & Clinical Redaction), Sagar G A (Verification, Final Tweaks & Report Preparation) · Repo: https://github.com/Sudarshan25092007/Clarus

## 1. In one paragraph
Clarus is an ultra-fast, offline, CPU-only zero-LLM privacy firewall engineered for heterogeneous Indian healthcare data spanning 4 hospital networks, 3 languages (English, Hindi, Hinglish), and 5 modalities. It couples a **3-key orthogonal blocking and constrained graph clustering engine** (F0.5 = 1.00, 0 false merges) with a **bounded mask-and-restore clinical shield** and **multi-channel PII elimination engine** that achieves **0.0% PII leakage (0/3,230)**, eliminates **100% of planted canaries (0/4)**, and retains **100.0% of clinical medical data (4,855/4,855)** in **4.4s runtime**.

## 2. Architecture
```
dev/input/ (200 files: CSV, TSV, JSON, TXT)
    │
    ▼
[Pass 1: Global Discovery]
    ├── Multi-Encoding Stream Decoders (ASCII, UTF-8-BOM, UTF-16LE/BE, CP1252)
    ├── Cross-Hospital Entity Extraction (UHIDs, Phones, Names, Relatives, Addresses)
    ├── 3-Key Orthogonal Blocking (Exact Phone, Name+Gender, Relative+Relation+Gender)
    ├── None-Safe Pairwise Feature Engine & Household Guards
    └── Score-Ordered Constrained Union-Find (Lazy Anti-Transitivity Verification)
    │
    ▼
[Pass 2: Sanitization & Export]
    ├── Keyed HMAC-SHA256 Pseudonyms (PSN-XXXXXX) & Per-Patient Date Shifting
    ├── Bounded Mask-and-Restore Clinical Shield (__SHIELD_i__ tokens)
    ├── Multi-Channel PII Redactor (Content, Filenames, Headers, JSON Keys, Base64)
    └── Safe Quarantine Engine (Non-revealing reason codes)
    │
    ▼
sanitized/ (192 clean files) + records.jsonl (821 records) + report.json (raksha-report/1)
```

## 3. How it works
- **Ingestion:** Streams CSV, TSV, JSON, and TXT files across four character encodings without third-party libraries. Malicious, hostile, or broken files (symlink directory traversals, arbitrary-key recursive depth bombs, corrupt bytes, mislabeled headers) are safely quarantined with non-revealing error codes (`symlink_traversal`, `excessive_depth`, `corrupt_bytes`, `mislabeled_format`).
- **Detection:** Two-pass identity-driven architecture. Pass 1 extracts all patient names, relatives, phones, and UHIDs across registrations, OPD logs, and billing files into an in-memory gazetteer. Pass 2 executes bounded regex scrubbing across Latin, Devanagari numerals (`[६-९][\u0966-\u096F]{9}`), and Hinglish clinical narratives (`ka BP`, `ko bukhar hai`).
- **Linkage & Pseudonyms:** 3-key orthogonal blocking (exact phone, name+gender, relative+relation+gender; never UHID alone) reduces candidate pairs by 99.0% (22 pairs from 2,145) with 100% candidate recall. Constrained graph clustering prevents transitive over-merging by enforcing cross-pair anti-transitivity verification before joining components. Deterministic HMAC-SHA256 assigns collision-free Crockford Base32 pseudonyms (`PSN-XXXXXX`).
- **Privacy Layer:** Date shifts uniformly displace temporal events per-patient within `[-max, +max]`, preserving chronological stay duration. Age values are banded into 5-year cohorts (widened to 10-year cohorts with `tehsil` suppressed as `[SUPPRESSED]` when distinct-patient cohort size `< min_group_size`). Location fields retain district context without village/street exposure.
- **Security:** Zero secret leakage; `RAKSHA_SECRET` resides purely in volatile memory. All stdout/stderr streams are stripped of PII.

## 4. USP
- **Zero-Backdoor Bounded Shielding:** Shields atomic clinical tokens (ICD-10, dosages, vitals, eponyms, attending doctors) without line-spanning greedy regexes, eliminating canary hiding vectors.
- **Household & Homonym Guards:** Isolates relatives sharing a single mobile number unless name/relationship tokens independently corroborate identity.
- **Multi-Channel Coverage:** Scrubs filenames (with collision-guarded path hashes), dictionary keys, and standard/URL-safe base64 attachment payloads.

## 5. Privacy–utility trade-off (measured on the practice data)

| Measure | Starter Baseline | Clarus Final | Self-Check Benchmark |
|---|---|---|---|
| Personal data leaked (3,230 items) | 84.1% (2,718 leaked) | **0.0% (0 leaked)** | < 10.0% |
| Planted canaries leaked (4 canaries) | 4 / 4 leaked (-12 pts) | **0 / 4 leaked** | 0 (Disqualification risk) |
| Medical values kept (4,855 items) | 100.0% | **100.0% (4,855 kept)** | 100.0% |
| Patient linking precision | 0.00 | **1.00 (0 false merges)** | 1.00 |
| Patient linking recall | 0.00 | **1.00** | High |
| Patient linking F0.5 | 0.00 | **1.00** | High |
| Raw unsupported copies | 0 | **0** | 0 |
| Run time / Peak memory | ~1.2s / 24 MB | **4.4s / 28.4 MB** | < 900s / < 4,096 MB |
| Output contract compliance | OK | **CONTRACT OK** | Required |

## 6. Known limitations & Roadmap
1. Unseen transliteration variants with no phone or UHID overlap currently fall back to conservative isolation to guarantee zero false merges.
2. Uniform per-patient date shifting preserves duration of stay but could be enhanced with differential jitter for non-clinical milestones.
3. Rare-group k-anonymity (`min_group_size: 5` in `policy.yaml`) is enforced structurally in Pass 2 (`tehsil` -> `[SUPPRESSED]`, rare cohorts widened to 10-year age bands, alongside equivalence-class profiling in `identity_risk.py`).

