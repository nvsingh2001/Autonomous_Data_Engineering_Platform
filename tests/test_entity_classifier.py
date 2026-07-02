import unittest
from tools.entity_classifier import EntityClassifier, ECommerceEntity

class TestEntityClassifier(unittest.TestCase):

    def test_classify_orders(self):
        columns = ["order_id", "customer_id", "gross_amount", "order_date"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.ORDERS)
        self.assertTrue(result["confidence"] > 0.4)

    def test_classify_products(self):
        columns = ["product_id", "product_name", "product_category_name", "price", "description"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.PRODUCTS)
        self.assertIn("product_id", result["matched_signals"])

    def test_classify_order_items(self):
        columns = ["order_id", "product_id", "quantity", "unit_price", "line_total"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.ORDER_ITEMS)

    def test_classify_customers(self):
        columns = ["customer_id", "first_name", "email", "address", "zip_code"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.CUSTOMERS)

    def test_normalization_and_abbreviation(self):
        # tests abbreviation 'qty' -> 'quantity', 'prod' -> 'product'
        columns = ["ord_id", "prod_id", "qty", "prc"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.ORDER_ITEMS)

    def test_disqualify_logic(self):
        # A file with 'product_id' might look like products, but 'order_id' disqualifies it
        # from being PRODUCTS because PRODUCTS grain is one row per product, not per order
        columns = ["product_id", "product_name", "order_id", "gross_amount"]
        result = EntityClassifier.classify(columns)
        self.assertNotEqual(result["entity"], ECommerceEntity.PRODUCTS)

    def test_unknown_entity(self):
        columns = ["random_col1", "foo", "bar", "unknown_metric"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.UNKNOWN)
        self.assertEqual(result["confidence"], 0.0)

    def test_filename_influence(self):
        # By columns alone, it might just look like some generic data
        # But filename provides a strong hint
        columns = ["id", "name", "total", "date"]
        # Without filename
        result1 = EntityClassifier.classify(columns)
        # With filename
        result2 = EntityClassifier.classify(columns, filename="expenses_2023.csv")
        
        self.assertEqual(result2["entity"], ECommerceEntity.FINANCIALS)

    def test_ambiguity_notes(self):
        # Provide signals that strongly match multiple entities
        # e.g., 'order_id', 'review_id', 'review_score' -> REVIEWS vs something else
        columns = ["order_id", "review_id", "review_score", "customer_id", "comment"]
        result = EntityClassifier.classify(columns)
        self.assertEqual(result["entity"], ECommerceEntity.REVIEWS)
        # We don't assert ambiguity string directly, just that notes exists and is not failing
        self.assertIsInstance(result["notes"], str)

if __name__ == "__main__":
    unittest.main()
