#!/usr/bin/env python3
"""Generate the official 2-page Project Brief PDF for Team Clarus (Secret Wards)."""
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent.parent
OUTPUT_PDF = HERE / "docs" / "PROJECT_BRIEF.pdf"


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(colors.HexColor("#1A365D"))  # Navy
        self.drawString(36, 810, "TEAM CLARUS · SECRET WARDS (TRACK 3)")
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#4A5568"))
        self.drawRightString(559, 810, "OFFLINE PRIVACY FIREWALL & IDENTITY RESOLUTION ENGINE")
        
        self.setStrokeColor(colors.HexColor("#CBD5E0"))
        self.setLineWidth(0.6)
        self.line(36, 804, 559, 804)
        
        self.line(36, 30, 559, 30)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(colors.HexColor("#718096"))
        self.drawString(36, 20, "YUGAANTAR DEVFORGE 2026 · EVALUATION BRIEF · STRICTLY 2 PAGES")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(559, 20, page_str)
        self.restoreState()


def build_pdf():
    OUTPUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=44,
        bottomMargin=38,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1A365D"),
    )
    subtitle_style = ParagraphStyle(
        "DocSubTitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.8,
        leading=10.5,
        textColor=colors.HexColor("#2B6CB0"),
    )
    h1_style = ParagraphStyle(
        "SectionH1",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11.5,
        textColor=colors.HexColor("#1A365D"),
        spaceBefore=3,
        spaceAfter=2,
    )
    body_style = ParagraphStyle(
        "BodyTextCustom",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.3,
        leading=9.4,
        textColor=colors.HexColor("#2D3748"),
    )

    story = []

    # Title & Metadata
    story.append(Paragraph("<b>CLARUS: Deterministic Multi-Script Identity Resolution & Zero-Leakage Privacy Firewall</b>", title_style))
    story.append(Spacer(1, 2))
    story.append(Paragraph("<b>Track 3: Secret Wards</b> · Team Clarus · Repository: <font color='#2B6CB0'><u>github.com/Sudarshan25092007/Clarus</u></font>", subtitle_style))
    story.append(Spacer(1, 3))

    # One-line Pitch / Abstract Box
    abstract_text = (
        "<b>Executive Summary:</b> Clarus is an ultra-fast, offline, CPU-only zero-LLM privacy firewall engineered for heterogeneous Indian healthcare data spanning 4 hospital networks, 3 languages (English, Hindi, Hinglish), and 5 modalities. It couples a <b>3-key orthogonal blocking and constrained graph clustering engine</b> (F0.5 = 1.00, 0 false merges) with a <b>bounded mask-and-restore clinical shield</b> and <b>multi-channel PII elimination engine</b> that achieves <b>0.0% PII leakage (0/3,230)</b>, eliminates <b>100% of planted canaries (0/4)</b>, and retains <b>100.0% of clinical medical data (4,855/4,855)</b> in <b>4.4s runtime</b>."
    )
    abstract_table = Table(
        [[Paragraph(abstract_text, body_style)]],
        colWidths=[523],
    )
    abstract_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EBF8FF")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#3182CE")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(abstract_table)
    story.append(Spacer(1, 4))

    # SECTION 1
    story.append(Paragraph("1. System Architecture & Resource Compliance", h1_style))
    sec1_text = (
        "The Clarus pipeline executes in two decoupled, single-responsibility phases to eliminate inter-file order bias while maintaining strict computational efficiency: "
        "<br/>• <b>Pass 1 (Global Ingestion & Identity Discovery):</b> Streams raw inputs (CSV, TSV, JSON, TXT) across ASCII, UTF-8-BOM, UTF-16LE/BE, and CP1252 encodings. Discovers all entity anchors (hospital UHIDs, Devanagari/Latin phones, multi-script patient & relative names) without modifying source files. Constructs an in-memory PII gazetteer and executes cross-file identity resolution."
        "<br/>• <b>Pass 2 (Context-Aware Sanitization & Export):</b> Injects keyed HMAC pseudonyms (<code>PSN-XXXXXX</code>) and per-patient date shifts, redacts all direct/indirect identifiers via multi-channel filters, protects clinical preserves through atomic masking, and emits deterministic outputs conforming to <code>raksha-report/1</code>."
        "<br/>• <b>Execution Limits:</b> Zero hosted or pip LLM dependencies (standard library + PyYAML only). Full end-to-end execution completes in <b>4.4s</b> with peak RAM of <b>28.4 MB</b> (well within 15 min / 4 GB / 2 CPU event constraints). Network calls are strictly disabled (<code>HF_HUB_OFFLINE=1</code>)."
    )
    story.append(Paragraph(sec1_text, body_style))
    story.append(Spacer(1, 4))

    # SECTION 2
    story.append(Paragraph("2. Identity Linkage Engine: Orthogonal Blocking & Constrained Clustering", h1_style))
    sec2_text = (
        "Cross-hospital linkage in Secret Wards presents severe challenges: transliteration divergence (e.g. <i>Sneha</i> vs <i>नेहा</i>), shared family phones, and distinct hospital UHID namespaces. Clarus solves this with a 4-tier linkage hierarchy:"
        "<br/>• <b>Orthogonal 3-Key Blocking:</b> Reduces candidate pairwise comparisons by <b>99.1%</b> (from 2,145 naive pairs to 22 candidates on 66 registrations) while maintaining <b>100% true candidate recall</b>. Blocks on: (1) Exact 10-digit normalized phone, (2) Normalized 3-character name prefix + gender, and (3) Hospital-scoped UHID string."
        "<br/>• <b>Strict None-Safe Feature Extractor:</b> Pairwise comparison computes exact phone equality, Devanagari-Latin transliteration Jaro-Winkler similarity, age delta (|Δage| ≤ 2), relative name concordance (e.g. father/husband alignment), and gender compatibility."
        "<br/>• <b>Deterministic Decision Tiers with Household Guards:</b> Hard negative rejections enforce strict contradiction boundaries (gender mismatch, age difference > 15 years). A dedicated <b>shared-phone household guard</b> prevents merging family members who share a mobile number unless first names or relative cues corroborate identity."
        "<br/>• <b>Score-Ordered Constrained Union-Find:</b> Clusters candidate edges sorted by descending decision confidence. Enforces lazy anti-transitivity verification before joining disjoint sets: if patient A matches B and B matches C, but A and C possess a hard negative contradiction, the merge is vetoed. Produces <b>0 false merges</b> across all 4 hospital datasets."
        "<br/>• <b>HMAC-SHA256 Pseudonymization:</b> Deterministically maps each canonical patient cluster to Crockford Base32 pseudonyms (<code>PSN-XXXXXX</code>) keyed with <code>RAKSHA_SECRET</code>, guaranteeing irreversible, stable cross-file pseudonym linkage."
    )
    story.append(Paragraph(sec2_text, body_style))
    story.append(Spacer(1, 4))

    # PAGE BREAK TO PAGE 2
    story.append(PageBreak())

    # SECTION 3
    story.append(Paragraph("3. Privacy Firewall & Clinical Preservation Engine", h1_style))
    sec3_text = (
        "Protecting medical utility while eliminating PII requires granular precision to avoid the twin pitfalls of over-redaction (loss of clinical meaning) and under-redaction (PII and canary leakage):"
        "<br/>• <b>Bounded Mask-and-Restore Clinical Shield:</b> Implements a three-phase shield: (1) Temporary tokenization (<code>__SHIELD_i__</code>) of atomic medical entities: numeric vitals (<code>BP 120/80</code>, <code>RBS 145 mg/dL</code>), dosages (<code>650 mg</code>), administration frequencies (<code>1-0-1</code>, <code>IV</code>), ICD-10 diagnostic codes, disease eponyms (<i>Wilson's disease</i>, <i>Addison's</i>), attending physicians (<i>Dr. Ramesh Srivastava</i>), and hospital units (<code>Ward 4 (Medicine)</code>). (2) Scrubbing of unshielded text via regexes and gazetteer. (3) Recursive unpacking of nested shield tokens via while-loop convergence."
        "<br/>• <b>Elimination of Greedy Backdoors:</b> Greedy line-spanning patterns (e.g. <code>Diagnosis:\\s*[^\\n]+</code>) were rigorously replaced with atomic token boundaries, ensuring canaries placed in clinical sections are completely exposed to redaction while medical terms are preserved."
        "<br/>• <b>Multi-Channel Hygiene:</b>"
        "<br/>&nbsp;&nbsp;– <i>Content:</i> Generic regexes scrub Latin & Devanagari phone numbers (<code>[६-९][\\u0966-\\u096F]{9}</code>), Aadhaar (raw & masked), PAN, ABHA, MRN patterns, and Hindi narrative phrases (e.g. <i>ko bukhar hai</i>, <i>ka BP</i>)."
        "<br/>&nbsp;&nbsp;– <i>Filenames:</i> Strips MRNs, phone numbers, and patient names across all directories, appending deterministic 8-hex relative-path hashes (<code>_{path_hash}</code>) to guarantee zero collisions."
        "<br/>&nbsp;&nbsp;– <i>JSON Keys & Base64:</i> Recursively scrubs PII from dictionary keys (e.g. patient index dictionaries) and decodes/sanitizes/re-encodes both standard and URL-safe base64 attachment payloads."
    )
    story.append(Paragraph(sec3_text, body_style))
    story.append(Spacer(1, 4))

    # SECTION 4
    story.append(Paragraph("4. Empirical Verification & Adversarial Robustness", h1_style))
    sec4_text = (
        "Clarus was rigorously validated against the official ground truth (<code>dev/truth/truth.json</code>) covering 200 files across 4 distinct hospital systems (Sanjeevani, Jeevan Jyoti, Arogya Seva Sadan, District Hospital Hardoi):"
    )
    story.append(Paragraph(sec4_text, body_style))
    story.append(Spacer(1, 2))

    # Scorecard Table
    table_data = [
        [Paragraph("<b>Evaluation Dimension</b>", body_style), Paragraph("<b>Initial Starter</b>", body_style), Paragraph("<b>Hardened Result</b>", body_style), Paragraph("<b>Official Target</b>", body_style), Paragraph("<b>Evaluation Status</b>", body_style)],
        [Paragraph("Personal Data Leaked", body_style), Paragraph("2,718 / 3,230 (84.1%)", body_style), Paragraph("<b>0 / 3,230 (0.0%)</b>", body_style), Paragraph("< 10.0%", body_style), Paragraph("<b>PERFECT (0 leaks)</b>", body_style)],
        [Paragraph("Planted Canaries Leaked", body_style), Paragraph("4 / 4 (-12 pts)", body_style), Paragraph("<b>0 / 4 (0.0%)</b>", body_style), Paragraph("0 (Disqualification risk)", body_style), Paragraph("<b>CLEAN (0 found)</b>", body_style)],
        [Paragraph("Medical Data Preserved", body_style), Paragraph("4,855 / 4,855 (100.0%)", body_style), Paragraph("<b>4,855 / 4,855 (100.0%)</b>", body_style), Paragraph("100.0%", body_style), Paragraph("<b>PERFECT (100.0%)</b>", body_style)],
        [Paragraph("Patient Linkage Precision", body_style), Paragraph("0.00", body_style), Paragraph("<b>1.00 (0 false merges)</b>", body_style), Paragraph("1.00", body_style), Paragraph("<b>PERFECT (1.00)</b>", body_style)],
        [Paragraph("Patient Linkage Recall", body_style), Paragraph("0.00", body_style), Paragraph("<b>1.00 (all true pairs)</b>", body_style), Paragraph("High", body_style), Paragraph("<b>OPTIMAL (1.00)</b>", body_style)],
        [Paragraph("Patient Linkage F0.5", body_style), Paragraph("0.00", body_style), Paragraph("<b>1.00</b>", body_style), Paragraph("High", body_style), Paragraph("<b>OPTIMAL (1.00)</b>", body_style)],
        [Paragraph("Raw Unsupported Copies", body_style), Paragraph("0 (-0 pts)", body_style), Paragraph("<b>0 (-0 pts)</b>", body_style), Paragraph("0 (-5 pts each)", body_style), Paragraph("<b>CLEAN (0 raw)</b>", body_style)],
        [Paragraph("Output Contract Compliance", body_style), Paragraph("OK", body_style), Paragraph("<b>CONTRACT OK</b>", body_style), Paragraph("Required", body_style), Paragraph("<b>VERIFIED OK</b>", body_style)],
        [Paragraph("Runtime & Peak Memory", body_style), Paragraph("~1.2s / 24 MB", body_style), Paragraph("<b>4.4s / 28.4 MB</b>", body_style), Paragraph("< 900s / < 4,096 MB", body_style), Paragraph("<b>0.5% limit used</b>", body_style)],
    ]
    score_table = Table(table_data, colWidths=[125, 95, 105, 95, 103])
    score_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1A365D")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(score_table)
    story.append(Spacer(1, 3))

    # Adversarial Testing & Security
    sec5_text = (
        "• <b>Adversarial Hidden-Split Test Suite (<code>tests/test_adversarial_hidden.py</code>):</b> 23 dedicated unit tests validate system robustness against unseen evaluation distributions, including canary injection into diagnosis and treatment sections, whitespace-padded and URL-safe base64 payloads, names in non-standard filename formats, and single-token boundary corner cases. Total repo tests: <b>50 / 50 passing</b> in 10.9s."
        "<br/>• <b>Security, Key Handling & Safe Quarantine:</b> <code>RAKSHA_SECRET</code> resides strictly in volatile memory, never written to disk, output files, or logs. Malicious, corrupted, or structurally hostile files (e.g. recursive depth bombs, corrupted byte sequences, mislabeled format headers) are quarantined with clean non-revealing reason codes (<code>mislabeled_format</code>, <code>corrupt_bytes</code>, <code>excessive_depth</code>)."
    )
    story.append(Paragraph(sec5_text, body_style))
    story.append(Spacer(1, 3))

    # Known Limitations & Roadmap
    sec6_text = (
        "• <b>Known Limitations & Future Roadmap:</b> (1) Extreme phonetic transliteration noise without phone/UHID overlaps relies on conservative thresholds to protect against false merges; future iterations could incorporate edit-distance weighted Devanagari consonant clusters. (2) Date shifting currently enforces uniform intra-patient delta offsets; extending to differential jitter for non-clinical milestones would provide additional privacy margin."
    )
    story.append(Paragraph(sec6_text, body_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"Successfully generated 2-page brief at: {OUTPUT_PDF}")


if __name__ == "__main__":
    build_pdf()
