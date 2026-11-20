"""Test query validation, FastAPI, and optionally the sample PostgreSQL database."""

from decimal import Decimal
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch

from fastapi.testclient import TestClient
import psycopg

from assistant import EXAMPLES, run_query
from main import app
from sql_guard import UnsafeSQL, validate_sql


class SQLTests(TestCase):
    def test_each_demo_query_passes(self):
        for query in EXAMPLES.values():
            with self.subTest(query=query):
                self.assertIn("FROM reporting.sales", validate_sql(query))

    def test_preserves_small_limit_and_caps_large_limit(self):
        base = "SELECT product FROM reporting.sales"
        for suffix, expected in [("", 101), (" LIMIT 5", 5), (" LIMIT 5000", 101)]:
            self.assertTrue(validate_sql(base + suffix).endswith(f"LIMIT {expected}"))

    def test_denies_queries_outside_reporting_scope(self):
        queries = [
            "DELETE FROM reporting.sales",
            "SELECT product FROM reporting.sales; SELECT region FROM reporting.sales",
            "SELECT product INTO TEMP scratch FROM reporting.sales",
            "SELECT product FROM shop.orders",
            "SELECT product FROM reporting.sales FOR UPDATE",
            "SELECT lower(product) FROM reporting.sales",
            "SELECT missing_column FROM reporting.sales",
            "SELECT product FROM reporting.sales UNION SELECT product FROM reporting.sales",
            "SELECT product FROM reporting.sales WHERE order_id IN (SELECT order_id FROM reporting.sales)",
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(UnsafeSQL):
                validate_sql(query)


class APITests(TestCase):
    def test_example_flows_through_validator_to_executor(self):
        with patch.dict(os.environ, {"DEMO_MODE": "true"}), patch(
            "main.run_query", return_value=(["total_revenue_inr"], [(Decimal("102700.00"),)], False),
        ) as executor, TestClient(app) as client:
            self.assertEqual(client.get("/").status_code, 200)
            self.assertTrue(client.get("/api/examples").json()["demo_mode"])
            response = client.post("/api/ask", json={"question": "What is the total revenue?"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rows"], [["102700.00"]])
            self.assertIn("102700.00", response.json()["summary"])
            self.assertTrue(executor.call_args.args[0].endswith("LIMIT 101"))

    def test_rejected_sql_never_reaches_postgres(self):
        with patch("main.make_sql", return_value="DELETE FROM reporting.sales"), patch(
            "main.run_query",
        ) as executor, TestClient(app) as client:
            self.assertEqual(client.post("/api/ask", json={"question": "Clear the sales."}).status_code, 422)
            executor.assert_not_called()


@skipUnless(os.getenv("RUN_DB_TESTS") == "1", "Start the sample database and set RUN_DB_TESTS=1")
class PostgreSQLTests(TestCase):
    def test_real_postgres_returns_known_example_results(self):
        expected = {
            "What is the total revenue?": [["102700.00"]],
            "Show revenue by region.": [
                ["South", "43700.00"], ["West", "30400.00"],
                ["North", "22500.00"], ["East", "6100.00"],
            ],
            "Which product sold the most units?": [["Mouse", 14]],
            "How many orders were placed in February 2025?": [[4]],
        }
        with patch.dict(os.environ, {"DEMO_MODE": "true"}), TestClient(app) as client:
            for question, rows in expected.items():
                with self.subTest(question=question):
                    response = client.post("/api/ask", json={"question": question})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["rows"], rows)

    def test_reader_cannot_read_the_base_table(self):
        # Call the executor directly to check the database's privileges independently.
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            run_query("SELECT * FROM shop.orders")

    def test_database_transaction_is_read_only(self):
        _, rows, _ = run_query("SHOW transaction_read_only")
        self.assertEqual(rows, [("on",)])
