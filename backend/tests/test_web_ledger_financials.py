"""Dependency-free unit tests for the pure Web Ledger financial aggregation helper."""
import ast
from pathlib import Path
import unittest

SERVER = Path(__file__).resolve().parents[1] / "server.py"
TREE = ast.parse(SERVER.read_text(encoding="utf-8"))
NODE = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_web_ledger_financial_totals")
namespace = {}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(SERVER), "exec"), namespace)
totals = namespace["_web_ledger_financial_totals"]


class WebLedgerFinancialTotalsTests(unittest.TestCase):
    def test_sales_received_and_outstanding(self):
        self.assertEqual(totals([{"id":"a","grand_total":1000},{"id":"b","grand_total":250}], {"a":400,"b":50}),
                         {"sales":1250.0,"received":450.0,"outstanding":800.0})

    def test_no_receipts(self):
        self.assertEqual(totals([{"id":"a","grand_total":999.99}], {}),
                         {"sales":999.99,"received":0,"outstanding":999.99})

    def test_overpayment_never_negative_outstanding(self):
        self.assertEqual(totals([{"id":"a","grand_total":100}], {"a":125}),
                         {"sales":100.0,"received":125.0,"outstanding":0})


if __name__ == "__main__":
    unittest.main()
