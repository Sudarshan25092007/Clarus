"""Unit tests for quasi-identifier risk and k-anonymity analyzer."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "starter"))

from identity_risk import compute_quasi_identifier_risk, is_k_anonymous


class TestQuasiIdentifierRisk(unittest.TestCase):
    def test_empty_records(self):
        res = compute_quasi_identifier_risk([])
        self.assertEqual(res["total_records"], 0)
        self.assertEqual(res["num_equivalence_classes"], 0)
        self.assertEqual(res["min_k"], 0)
        self.assertEqual(res["at_risk_records_count"], 0)
        self.assertEqual(res["safe_records_pct"], 100.0)

    def test_k_anonymity_metrics(self):
        records = [
            {"age_band": "20-24", "gender": "M"},
            {"age_band": "20-24", "gender": "M"},
            # Group 2: size 5
            {"age_band": "30-34", "gender": "F"},
            {"age_band": "30-34", "gender": "F"},
            {"age_band": "30-34", "gender": "F"},
            {"age_band": "30-34", "gender": "F"},
            {"age_band": "30-34", "gender": "F"},
            # Group 3: size 3
            {"age_band": "40-44", "gender": "M"},
            {"age_band": "40-44", "gender": "M"},
            {"age_band": "40-44", "gender": "M"},
        ]
        # At min_group_size = 5:
        # Group 1 (2 records) and Group 3 (3 records) are at risk (< 5).
        res5 = compute_quasi_identifier_risk(records, min_group_size=5)
        self.assertEqual(res5["total_records"], 10)
        self.assertEqual(res5["num_equivalence_classes"], 3)
        self.assertEqual(res5["min_k"], 2)
        self.assertEqual(res5["at_risk_records_count"], 5)
        self.assertEqual(res5["at_risk_records_pct"], 50.0)
        self.assertEqual(res5["safe_records_pct"], 50.0)
        self.assertIn("20-24-m", res5["rare_classes"])
        self.assertIn("40-44-m", res5["rare_classes"])

        # At min_group_size = 3:
        # Only Group 1 (2 records) is at risk (< 3).
        res3 = compute_quasi_identifier_risk(records, min_group_size=3)
        self.assertEqual(res3["at_risk_records_count"], 2)
        self.assertEqual(res3["at_risk_records_pct"], 20.0)
        self.assertEqual(res3["safe_records_pct"], 80.0)
        self.assertEqual(res3["rare_classes"], {"20-24-m": 2})

        # Test is_k_anonymous helper
        self.assertTrue(is_k_anonymous(records, k=2))
        self.assertFalse(is_k_anonymous(records, k=3))
        self.assertFalse(is_k_anonymous(records, k=5))

    def test_custom_qi_fields(self):
        records = [
            {"facility": "SNJ", "age_band": "50-54", "gender": "M"},
            {"facility": "SNJ", "age_band": "50-54", "gender": "M"},
            {"facility": "JJH", "age_band": "50-54", "gender": "M"},
        ]
        res = compute_quasi_identifier_risk(
            records,
            min_group_size=2,
            qi_fields=("facility", "age_band", "gender"),
        )
        self.assertEqual(res["total_records"], 3)
        self.assertEqual(res["num_equivalence_classes"], 2)
        self.assertEqual(res["min_k"], 1)
        self.assertEqual(res["at_risk_records_count"], 1)
        self.assertEqual(res["rare_classes"], {"jjh-50-54-m": 1})


if __name__ == "__main__":
    unittest.main()
