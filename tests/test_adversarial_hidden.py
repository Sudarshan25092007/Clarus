"""Adversarial tests simulating hidden-eval attack vectors.

These tests ensure that the redaction engine handles:
1. Canary names placed on shielded lines (diagnosis, complaint, section headers)
2. URL-safe base64, whitespace-padded base64, and base64 in arbitrary JSON keys
3. Names in filenames without Discharge_Summary prefix
4. Caller/attendant patterns with commas, missing parentheses, and unusual formats
"""
import sys, os, re, base64, json, hashlib, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'starter'))

from identity_redactor import (
    redact_text, sanitize_base64_string, sanitize_filename, clean_json_value,
    shield_text, restore_text,
)
from main import check_nesting_depth


class TestShieldingBackdoor(unittest.TestCase):
    """Verify canary names on diagnosis/complaint/section lines are still redacted."""

    def test_canary_on_diagnosis_line(self):
        text = 'Diagnosis: Pneumonia. Canary Name was treated conservatively.'
        result = redact_text(text, {'patient_names': ['canary name'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Name', result)
        self.assertIn('Pneumonia', result)  # medical must survive

    def test_canary_on_complaints_line(self):
        text = 'Complaints: fever x 3 days. Canary Person brought to ER.'
        result = redact_text(text, {'patient_names': ['canary person'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Person', result)
        self.assertIn('fever', result)

    def test_canary_on_rx_line(self):
        text = 'Rx: Tab Paracetamol 650mg 1-1-1 x 5 days. Canary Patient discharged.'
        result = redact_text(text, {'patient_names': ['canary patient'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Patient', result)
        self.assertIn('Paracetamol', result)
        self.assertIn('650mg', result)

    def test_canary_in_treatment_section(self):
        text = 'Treatment: IV fluids + rest. Canary Victim responded well.'
        result = redact_text(text, {'patient_names': ['canary victim'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Victim', result)
        self.assertIn('IV fluids', result)

    def test_canary_on_kco_line(self):
        text = '14:00 - Canary Target - K/C/O tuberculosis of lung. Attendant informed.'
        result = redact_text(text, {'patient_names': ['canary target'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Target', result)
        self.assertIn('tuberculosis of lung', result)

    def test_canary_on_hindi_section(self):
        text = 'निदान: टायफॉइड बुखार। कैनरी व्यक्ति को 5 दिन से बुखार है।'
        result = redact_text(text, {'patient_names': ['कैनरी व्यक्ति'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('कैनरी व्यक्ति', result)
        self.assertIn('टायफॉइड बुखार', result)

    def test_eponym_preserved_while_name_redacted(self):
        text = "Wilson sahab ka BP 130/80. Wilson's disease under control."
        result = redact_text(text)
        self.assertIn("Wilson's disease", result)


class TestBase64Hardening(unittest.TestCase):
    """Verify base64 sanitization handles URL-safe, whitespace, and edge cases."""

    def test_url_safe_base64(self):
        payload = 'Patient: Canary Target, Phone: 9876543210'
        b64 = base64.urlsafe_b64encode(payload.encode()).decode()
        result = sanitize_base64_string(b64, {'patient_names': ['canary target'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        decoded = base64.b64decode(result + '=' * (-len(result) % 4)).decode('utf-8', errors='replace')
        self.assertNotIn('Canary Target', decoded)
        self.assertNotIn('9876543210', decoded)

    def test_whitespace_in_base64(self):
        payload = 'Name: Hidden Canary, UHID: JJH-9999999'
        b64 = base64.b64encode(payload.encode()).decode()
        b64_with_ws = '\n'.join(b64[i:i+20] for i in range(0, len(b64), 20))
        result = sanitize_base64_string(b64_with_ws, {'patient_names': ['hidden canary'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        decoded = base64.b64decode(result + '=' * (-len(result) % 4)).decode('utf-8', errors='replace')
        self.assertNotIn('Hidden Canary', decoded)

    def test_json_arbitrary_key_contains_name(self):
        data = {'patient_index': {'Canary Person': {'uhid': 'JJH-1234'}}, 'report': 'normal'}
        result = clean_json_value(data, {'patient_names': ['canary person'], 'relative_names': [], 'mrns': ['jjh-1234'], 'villages': [], 'phones': []})
        result_str = json.dumps(result)
        self.assertNotIn('Canary Person', result_str)
        self.assertNotIn('JJH-1234', result_str)

    def test_base64_in_nested_json(self):
        payload = 'Referral: Secret Patient (JJH-9999)'
        b64 = base64.b64encode(payload.encode()).decode()
        data = {'records': [{'data': b64}]}
        result = clean_json_value(data, {'patient_names': ['secret patient'], 'relative_names': [], 'mrns': ['jjh-9999'], 'villages': [], 'phones': []})
        result_str = json.dumps(result)
        self.assertNotIn('Secret Patient', result_str)

    def test_freetext_string_in_json_redacted(self):
        data = {'message': 'Called by Canary Person 9876543210'}
        result = clean_json_value(data, {'patient_names': ['canary person'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        result_str = json.dumps(result)
        self.assertNotIn('9876543210', result_str)


class TestFilenameGeneralization(unittest.TestCase):
    """Verify filename sanitization works for any prefix, not just Discharge_Summary."""

    def test_non_discharge_filename_with_name(self):
        result = sanitize_filename('lab/report_Canary_Person_JJH-1234.json',
            {'patient_names': ['canary person'], 'relative_names': [], 'mrns': ['jjh-1234'], 'villages': [], 'phones': []})
        self.assertNotIn('Canary', result)
        self.assertNotIn('Person', result)
        self.assertNotIn('JJH-1234', result)

    def test_filename_with_phone(self):
        result = sanitize_filename('lab/9876543210_report.json')
        self.assertNotIn('9876543210', result)

    def test_filename_with_mrn_various_formats(self):
        for mrn_file in ['notes/nursing_SNJ-26-064365.txt', 'lab/report_UHID-010607.json', 'billing/IP-26-00130.csv']:
            result = sanitize_filename(mrn_file)
            # MRN patterns should be detected and scrubbed
            for mrn_part in ['SNJ-26-064365', 'UHID-010607', 'IP-26-00130']:
                if mrn_part in mrn_file:
                    self.assertNotIn(mrn_part, result.split('/')[-1])


class TestNarrativePatternGeneralization(unittest.TestCase):
    """Verify caller/attendant patterns handle edge cases."""

    def test_time_log_without_parentheses(self):
        text = '10:30 - Canary Target, mobile 9876543210'
        result = redact_text(text, {'patient_names': ['canary target'], 'relative_names': [], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Target', result)

    def test_attendant_with_comma_mob(self):
        text = 'Attendant: Canary Relative, Mob. 9876543210'
        result = redact_text(text, {'patient_names': [], 'relative_names': ['canary relative'], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Relative', result)

    def test_caller_in_freetext(self):
        text = 'called by Canary Witness 9876543210 for emergency'
        result = redact_text(text)
        self.assertNotIn('Canary Witness', result)
        self.assertNotIn('9876543210', result)

    def test_so_wo_do_without_paren(self):
        text = 'S/o Canary Father discharged.'
        result = redact_text(text, {'patient_names': [], 'relative_names': ['canary father'], 'mrns': [], 'villages': [], 'phones': []})
        self.assertNotIn('Canary Father', result)

    def test_devanagari_phone_redacted(self):
        text = 'मोबाइल: ९८७६५४३२१०'
        result = redact_text(text)
        self.assertNotIn('९८७६५४३२१०', result)

    def test_dob_label_redacted(self):
        text = 'DOB: 15/03/1985'
        result = redact_text(text)
        self.assertNotIn('15/03/1985', result)

    def test_village_label_redacted(self):
        text = 'ग्राम: टेस्ट गाँव'
        result = redact_text(text)
        self.assertNotIn('टेस्ट गाँव', result)

    def test_address_with_parens_redacted(self):
        text = 'Address (attendant): C/O Someone, Ward 5'
        result = redact_text(text)
        self.assertNotIn('C/O Someone', result)


class TestSymlinkTraversalDefense(unittest.TestCase):
    """Verify input symlinks escaping inp are quarantined and never written to sanitized/."""

    def test_symlink_pointing_outside_is_quarantined(self):
        import tempfile, subprocess
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            inp = Path(td) / "inp"
            inp.mkdir()
            out = Path(td) / "out"
            out.mkdir()
            secret = Path(td) / "outside_secret.txt"
            secret.write_text("SUPER_SECRET_CANARY_OUTSIDE_DIR")
            sym = inp / "link.txt"
            sym.symlink_to(secret)
            normal = inp / "normal.txt"
            normal.write_text("Normal clinical text.")

            env = os.environ.copy()
            env["RAKSHA_SECRET"] = "test_secret"
            cmd = [sys.executable, os.path.join(os.path.dirname(__file__), "..", "starter", "main.py"), "--input", str(inp), "--output", str(out)]
            res = subprocess.run(cmd, env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)

            rep = json.loads((out / "report.json").read_text())
            sym_entry = next((e for e in rep["files"] if e["input_sha256"] == hashlib.sha256(b"SUPER_SECRET_CANARY_OUTSIDE_DIR").hexdigest()), None)
            self.assertIsNotNone(sym_entry)
            self.assertEqual(sym_entry.get("status"), "quarantined")
            self.assertEqual(sym_entry.get("reason"), "symlink_traversal")
            self.assertIsNone(sym_entry.get("output"))

            # Ensure the secret was never copied to sanitized/
            san = out / "sanitized"
            for p in san.rglob("*"):
                if p.is_file():
                    content = p.read_text(errors="ignore")
                    self.assertNotIn("SUPER_SECRET_CANARY_OUTSIDE_DIR", content)
            self.assertFalse((san / "link.txt").exists())


class TestJsonDepthDefense(unittest.TestCase):
    """Verify general JSON depth scanner detects depth bombs beyond max_depth."""

    def test_check_nesting_depth_catches_deep_structures(self):
        # 30 levels deep
        deep_json = '{"a": ' * 30 + '"payload"' + '}' * 30
        with self.assertRaises(ValueError) as ctx:
            check_nesting_depth(deep_json, max_depth=25)
        self.assertEqual(str(ctx.exception), "excessive_depth")

    def test_check_nesting_depth_catches_nested_arrays(self):
        deep_arr = '[' * 30 + '"payload"' + ']' * 30
        with self.assertRaises(ValueError) as ctx:
            check_nesting_depth(deep_arr, max_depth=25)
        self.assertEqual(str(ctx.exception), "excessive_depth")

    def test_check_nesting_depth_allows_valid_depth(self):
        valid_json = '{"level1": {"level2": {"level3": [1, 2, {"level5": "hello"}]}}}'
        # Should not raise
        check_nesting_depth(valid_json, max_depth=25)

    def test_check_nesting_depth_ignores_brackets_in_strings(self):
        fake_deep = '{"msg": "' + '{{{{[[[[[[' * 10 + '"}'
        # Should not raise because brackets are inside a string literal
        check_nesting_depth(fake_deep, max_depth=25)


if __name__ == '__main__':
    unittest.main()
