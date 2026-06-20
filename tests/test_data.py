import unittest
import os
import polars as pl

class TestDataGeneration(unittest.TestCase):
    def setUp(self):
        self.data_dir = "data"
        self.files = ["orders.csv", "order_items.csv", "order_item_refunds.csv", "products.csv", "website_pageviews.csv", "website_sessions.csv"]

    def test_files_exist(self):
        for f in self.files:
            path = os.path.join(self.data_dir, f)
            self.assertTrue(os.path.exists(path), f"{f} does not exist.")

    def test_orders_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "orders.csv"), n_rows=1)
        expected = ["order_id", "created_at", "website_session_id", "user_id", "primary_product_id", "items_purchased", "price_usd", "cogs_usd"]
        self.assertEqual(list(df.columns), expected)

    def test_order_items_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "order_items.csv"), n_rows=1)
        expected = ["order_item_id", "created_at", "order_id", "product_id", "is_primary_item", "price_usd", "cogs_usd"]
        self.assertEqual(list(df.columns), expected)

    def test_refunds_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "order_item_refunds.csv"), n_rows=1)
        expected = ["order_item_refund_id", "created_at", "order_item_id", "order_id", "refund_amount_usd"]
        self.assertEqual(list(df.columns), expected)

    def test_products_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "products.csv"), n_rows=1)
        expected = ["product_id", "created_at", "product_name"]
        self.assertEqual(list(df.columns), expected)

    def test_pageviews_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "website_pageviews.csv"), n_rows=1)
        expected = ["website_pageview_id", "created_at", "website_session_id", "pageview_url"]
        self.assertEqual(list(df.columns), expected)

    def test_sessions_headers(self):
        df = pl.read_csv(os.path.join(self.data_dir, "website_sessions.csv"), n_rows=1)
        expected = ["website_session_id", "created_at", "user_id", "is_repeat_session", "utm_source", "utm_campaign", "utm_content", "device_type", "http_referer"]
        self.assertEqual(list(df.columns), expected)

if __name__ == "__main__":
    unittest.main()
