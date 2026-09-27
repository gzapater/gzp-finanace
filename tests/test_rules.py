import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gzp_finance.rules import apply_rules, normalize_concept


class RuleTests(unittest.TestCase):
    def test_normalize_removes_variable_data(self):
        value = "SHOP 1234 payment 19.99 EUR ref 5500"
        self.assertEqual(normalize_concept(value), "shop")

    def test_high_priority_rule_wins_and_lower_rule_fills_missing_field(self):
        tx = {
            "source_bank": "Example Bank",
            "concept_raw": "Example Shop 1234",
            "bank_type": "CARD",
            "bank_category": "Shopping",
            "direction": "gasto",
        }
        rules = [
            {
                "id": "specific",
                "priority": 100,
                "support": 10,
                "match": {"source_bank": "Example Bank", "concept_key": "example shop", "bank_type": "CARD"},
                "set": {"target_categoria_general": "A"},
            },
            {
                "id": "general",
                "priority": 80,
                "support": 20,
                "match": {"source_bank": "Example Bank", "concept_key": "example shop"},
                "set": {"target_categoria_general": "B", "target_subtipo": "A1"},
            },
        ]
        result = apply_rules(tx, rules)
        self.assertEqual(result["values"]["target_categoria_general"], "A")
        self.assertEqual(result["values"]["target_subtipo"], "A1")
        self.assertEqual(len(result["conflicts"]), 1)


if __name__ == "__main__":
    unittest.main()
