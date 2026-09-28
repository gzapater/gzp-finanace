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
            "amount_eur": -10.0,
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

    def test_confirmed_rule_can_use_amount_range(self):
        rules = [
            {
                "id": "btc-plan",
                "priority": 1000,
                "support": 1,
                "match": {
                    "source_bank": "Trade Republic",
                    "concept_key": "bitcoin",
                    "bank_type": "BUY",
                    "amount_cents_min": "-22000",
                    "amount_cents_max": "-18000",
                },
                "set": {"target_detalle": "Plan de ahorro bitcoin"},
            }
        ]
        tx = {
            "source_bank": "Trade Republic",
            "concept_raw": "Bitcoin",
            "amount_eur": -199.96,
            "bank_type": "BUY",
        }
        self.assertEqual(apply_rules(tx, rules)["values"]["target_detalle"], "Plan de ahorro bitcoin")

    def test_confirmed_rule_can_use_counterparty_iban(self):
        rules = [
            {
                "id": "own-transfer",
                "priority": 1000,
                "support": 1,
                "match": {
                    "source_bank": "Trade Republic",
                    "counterparty_iban": "ES0000000000000000000000",
                    "bank_type": "TRANSFER_INSTANT_INBOUND",
                },
                "set": {"target_categoria_general": "11. Mov Cuentas"},
            }
        ]
        tx = {
            "source_bank": "Trade Republic",
            "concept_raw": "GONZALO",
            "amount_eur": 400,
            "counterparty_iban": "ES0000000000000000000000",
            "bank_type": "TRANSFER_INSTANT_INBOUND",
        }
        self.assertEqual(apply_rules(tx, rules)["values"]["target_categoria_general"], "11. Mov Cuentas")

    def test_same_concept_can_be_disambiguated_by_exact_amount(self):
        rules = [
            {
                "id": "transfer",
                "priority": 120,
                "support": 13,
                "match": {"source_bank": "CaixaBank", "concept_key": "pag nominas", "amount_cents": "-40000"},
                "set": {"target_categoria_general": "Mov Cuentas"},
            },
            {
                "id": "rent",
                "priority": 120,
                "support": 14,
                "match": {"source_bank": "CaixaBank", "concept_key": "pag nominas", "amount_cents": "-82500"},
                "set": {"target_categoria_general": "Casa"},
            },
        ]
        tx_transfer = {"source_bank": "CaixaBank", "concept_raw": "PAG NOMINAS", "amount_eur": -400}
        tx_rent = {"source_bank": "CaixaBank", "concept_raw": "PAG NOMINAS", "amount_eur": -825}
        self.assertEqual(apply_rules(tx_transfer, rules)["values"]["target_categoria_general"], "Mov Cuentas")
        self.assertEqual(apply_rules(tx_rent, rules)["values"]["target_categoria_general"], "Casa")


if __name__ == "__main__":
    unittest.main()
