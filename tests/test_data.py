import unittest
import os
import pandas as pd

class TestDataGeneration(unittest.TestCase):
    def setUp(self):
        self.data_dir = "data"
        self.files = ["crm_customers.csv", "products.csv", "sales_transactions.csv", "support_logs.csv"]

    def test_files_exist(self):
        for f in self.files:
            path = os.path.join(self.data_dir, f)
            self.assertTrue(os.path.exists(path), f"{f} does not exist.")

    def test_crm_headers(self):
        df = pd.read_csv(os.path.join(self.data_dir, "crm_customers.csv"))
        expected = ["customer_id", "name", "email", "phone", "sign_up_date", "country"]
        self.assertEqual(list(df.columns), expected)

    def test_products_headers(self):
        df = pd.read_csv(os.path.join(self.data_dir, "products.csv"))
        expected = ["product_id", "product_name", "category", "price", "stock"]
        self.assertEqual(list(df.columns), expected)

    def test_sales_headers(self):
        df = pd.read_csv(os.path.join(self.data_dir, "sales_transactions.csv"))
        expected = ["transaction_id", "customer_id", "product_id", "quantity", "unit_price", "transaction_date"]
        self.assertEqual(list(df.columns), expected)

    def test_support_headers(self):
        df = pd.read_csv(os.path.join(self.data_dir, "support_logs.csv"))
        expected = ["log_id", "customer_id", "ticket_type", "rating", "status", "log_date"]
        self.assertEqual(list(df.columns), expected)

if __name__ == "__main__":
    unittest.main()
