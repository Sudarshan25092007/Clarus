"""Phase 3 unit tests and full dev/input linkage benchmark against truth.json."""
from __future__ import annotations

import itertools
import json
import sys
import time
import unittest
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starter.identity import (  # noqa: E402
    IdentityResolver,
    LINK_THRESHOLD,
    RecordIdentity,
    has_hard_conflict,
    normalize_identity,
    score_pair,
)
from tests.test_phase1_extract import _iter_dev_records  # noqa: E402


class Phase3ResolveTests(unittest.TestCase):
    def test_homonym_separation(self) -> None:
        a = normalize_identity(
            RecordIdentity(
                relpath="registration/jeevanjyoti_patients.tsv",
                loc="row:15",
                hospital_hint="jeevanjyoti",
                names=["Brijesh Yadav"],
                relatives=[("S/o", "Raju Gupta")],
                phones=["9973945793"],
                dob="1954-10-18",
                age=70,
                gender="M",
                mrns=["JJH-0076864"],
            )
        )
        b = normalize_identity(
            RecordIdentity(
                relpath="registration/jeevanjyoti_patients.tsv",
                loc="row:27",
                hospital_hint="jeevanjyoti",
                names=["Brijesh Yadav"],
                relatives=[("S/o", "Manoj Tiwari")],
                phones=["6939821446"],
                dob="1999-03-13",
                age=27,
                gender="M",
                mrns=["JJH-0077243"],
            )
        )
        self.assertTrue(has_hard_conflict(a, b))
        self.assertEqual(score_pair(a, b), float("-inf"))

        resolver = IdentityResolver()
        resolver.add_record(
            "reg_a.csv",
            "row:0",
            {"patient_name": "Ramesh Kumar", "dob": "1980-01-01", "gender": "M", "guardian": "S/o Mohan", "mobile": "9876543210"},
        )
        resolver.add_record(
            "reg_b.csv",
            "row:1",
            {"patient_name": "Ramesh Kumar", "dob": "1995-05-05", "gender": "M", "guardian": "S/o Sohan", "mobile": "9123456789"},
        )
        resolver.resolve_all()
        self.assertIsNone(resolver.get_patient_id("reg_a.csv", "row:0"))
        self.assertIsNone(resolver.get_patient_id("reg_b.csv", "row:1"))

    def test_cross_script_and_cross_hospital_merge(self) -> None:
        resolver = IdentityResolver()
        resolver.add_record(
            "registration/sanjeevani_registration.csv",
            "row:0",
            {
                "UHID": "SNJ/26/064001",
                "Patient Name": "Md. Arif",
                "Gender": "M",
                "Age": "42",
                "DOB": "10/03/1984",
                "Relation": "S/o",
                "Relative Name": "Salim",
                "Mobile": "9811122233",
                "PIN": "226001",
            },
        )
        resolver.add_record(
            "discharge/arogya_ds_ADM000999.txt",
            "doc",
            {},
            "Arogya Seva Sadan\nमरीज़ का नाम: मोहम्मद आरिफ   आयु: 42 वर्ष / पुरुष\nपिता का नाम: सलीम\nमोबाइल: ९८१११२२२३३",
        )
        resolver.resolve_all()
        pid_a = resolver.get_patient_id("registration/sanjeevani_registration.csv", "row:0")
        pid_b = resolver.get_patient_id("discharge/arogya_ds_ADM000999.txt", "doc")
        self.assertIsNotNone(pid_a)
        self.assertEqual(pid_a, pid_b)

    def test_same_hospital_opd_vs_ipd_mrn_allowed_while_same_series_vetoed(self) -> None:
        opd = normalize_identity(
            RecordIdentity(
                relpath="fhir/bundle.json",
                loc="doc",
                hospital_hint="sanjeevani",
                names=["Shabana Bano"],
                mrns=["SNJ/26/064545", "IPD-2026-00474"],
            )
        )
        note = normalize_identity(
            RecordIdentity(
                relpath="notes/nursing_ipd-2026-00474.txt",
                loc="doc",
                hospital_hint="sanjeevani",
                names=["Shabana Bano"],
                mrns=["IPD-2026-00474"],
            )
        )
        other_opd = normalize_identity(
            RecordIdentity(
                relpath="registration/sanjeevani_registration.csv",
                loc="row:9",
                hospital_hint="sanjeevani",
                names=["Shabana Bano"],
                mrns=["SNJ/26/064999"],
            )
        )
        self.assertFalse(has_hard_conflict(opd, note))
        self.assertGreaterEqual(score_pair(opd, note), LINK_THRESHOLD)
        self.assertTrue(has_hard_conflict(opd, other_opd))

    def test_transitive_conflict_guard(self) -> None:
        # A and B match strongly (same phone + name), B and C match strongly (same MRN + name),
        # but A and C have conflicting gender -> cluster {A, B} must NOT transitively merge with C.
        resolver = IdentityResolver()
        resolver.add_record(
            "a.csv",
            "row:0",
            {"patient_name": "Kiran Sharma", "phone": "9876500001", "gender": "F", "pin": "226001"},
        )
        resolver.add_record(
            "b.csv",
            "row:0",
            {"patient_name": "Kiran Sharma", "phone": "9876500001", "uhid": "SNJ/26/100001"},
        )
        resolver.add_record(
            "c.csv",
            "row:0",
            {"patient_name": "Kiran Sharma", "uhid": "SNJ/26/100001", "gender": "M", "pin": "226001"},
        )
        resolver.resolve_all()
        pid_a = resolver.get_patient_id("a.csv", "row:0")
        pid_b = resolver.get_patient_id("b.csv", "row:0")
        pid_c = resolver.get_patient_id("c.csv", "row:0")
        self.assertFalse(pid_a is not None and pid_a == pid_c)
        self.assertTrue(pid_a == pid_b or pid_b == pid_c)


def run_truth_benchmark() -> tuple[float, float, float, int, bool, float]:
    """Benchmark IdentityResolver across dev/input against dev/truth/truth.json."""
    dev_input = REPO_ROOT / "dev" / "input"
    truth = json.loads((REPO_ROOT / "dev" / "truth" / "truth.json").read_text(encoding="utf-8"))

    resolver = IdentityResolver()
    for item in _iter_dev_records(dev_input):
        if item[0] == "FAIL":
            continue
        _, _, rel, loc, raw_fields, raw_text = item
        resolver.add_record(rel, loc, raw_fields, raw_text)

    t0 = time.monotonic()
    resolver.resolve_all()
    elapsed = time.monotonic() - t0

    # Evaluate pairwise metrics strictly matching check_dev.py + docs/SCORING.md
    req_units: list[tuple[str, str | None]] = []
    all_linkable_or_ambig: list[tuple[str, str | None]] = []
    pt_to_preds: dict[str, set[str]] = defaultdict(set)

    for rel, fobj in truth["files"].items():
        for loc, u in fobj.get("units", {}).items():
            pt = u.get("patient")
            link = u.get("link")
            pred = resolver.get_patient_id(rel, loc)
            if pred and pt:
                pt_to_preds[pt].add(pred)
            if link == "required" and pt:
                req_units.append((pt, pred))
                all_linkable_or_ambig.append((pt, pred))
            elif link == "ambiguous" and pt:
                all_linkable_or_ambig.append((pt, pred))

    by_true: dict[str, list[int]] = defaultdict(list)
    for idx, (pt, _) in enumerate(req_units):
        by_true[pt].append(idx)
    true_pairs = sum(len(v) * (len(v) - 1) // 2 for v in by_true.values())

    by_pred: dict[str, list[int]] = defaultdict(list)
    for idx, (_, pred) in enumerate(all_linkable_or_ambig):
        if pred:
            by_pred[pred].append(idx)

    tp = fp = 0
    false_merges_detail: list[tuple[str, set[str]]] = []
    for pred, idxs in by_pred.items():
        pts = {all_linkable_or_ambig[i][0] for i in idxs}
        if len(pts) > 1:
            false_merges_detail.append((pred, pts))
        for i, j in itertools.combinations(idxs, 2):
            if all_linkable_or_ambig[i][0] == all_linkable_or_ambig[j][0]:
                tp += 1
            else:
                fp += 1

    fn = true_pairs - tp
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f05 = (1.25 * prec * rec / (0.25 * prec + rec)) if (prec + rec) else 0.0
    merged_people = len(false_merges_detail)

    homonym_pairs = truth["traps"]["homonym_pairs"]
    homonyms_ok = True
    homonym_report = []
    for p1, p2 in homonym_pairs:
        shared = pt_to_preds[p1] & pt_to_preds[p2]
        if shared:
            homonyms_ok = False
        homonym_report.append((p1, p2, sorted(pt_to_preds[p1]), sorted(pt_to_preds[p2]), sorted(shared)))

    # Summarize missed required units (unlinked or split within a patient)
    unlinked_by_folder: dict[str, int] = defaultdict(int)
    split_pts: list[tuple[str, list[str]]] = []
    for rel, fobj in truth["files"].items():
        for loc, u in fobj.get("units", {}).items():
            if u.get("link") == "required" and u.get("patient"):
                if not resolver.get_patient_id(rel, loc):
                    unlinked_by_folder[rel.split("/", 1)[0]] += 1
    for pt, preds in sorted(pt_to_preds.items()):
        if len(preds) > 1:
            split_pts.append((pt, sorted(preds)))

    print("\n=== Phase 3 Benchmark vs dev/truth/truth.json ===")
    print(f"Records loaded      : {len(resolver.records)}")
    print(f"Linked clusters     : {len(resolver.clusters)}")
    print(f"Required units      : {len(req_units)} (true pairs={true_pairs})")
    print(f"Precision / Recall  : {prec:.4f} / {rec:.4f}  (F0.5 = {f05:.4f})")
    print(f"TP / FP / FN        : {tp} / {fp} / {fn}")
    print(f"False merges        : {merged_people} {false_merges_detail}")
    print(f"Homonym pairs clean : {homonyms_ok}")
    for p1, p2, s1, s2, sh in homonym_report:
        print(f"  {p1} {s1} vs {p2} {s2} -> shared={sh}")
    print(f"Unlinked req units  : {sum(unlinked_by_folder.values())} {dict(sorted(unlinked_by_folder.items()))}")
    print(f"Split patients      : {len(split_pts)} {split_pts[:10]}")
    print(f"Runtime (CPU)       : {elapsed:.4f}s")

    return prec, rec, f05, merged_people, homonyms_ok, elapsed


if __name__ == "__main__":
    prec, rec, _, merged_people, homonyms_ok, _ = run_truth_benchmark()
    assert merged_people == 0, f"Expected 0 false merges, got {merged_people}"
    assert homonyms_ok, "Homonym pairs merged!"
    assert prec >= 0.99, f"Expected precision >= 0.99, got {prec}"
    assert rec > 0.50, f"Expected substantial recall, got {rec}"
    unittest.main(verbosity=2)
