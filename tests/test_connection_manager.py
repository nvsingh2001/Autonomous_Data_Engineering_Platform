import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import ConnectionManager

_ORDERS_CSV = """\
order_id,amount
1,10.00
2,20.00
3,30.00
"""


class TestConnectionManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_cm_test_")
        self.db_path = os.path.join(self.temp_dir, "warehouse.db")
        with open(os.path.join(self.temp_dir, "orders.csv"), "w") as f:
            f.write(_ORDERS_CSV)
        self.cm = ConnectionManager(self.db_path, self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_warehouse_without_sources(self):
        with self.cm.warehouse() as conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
            tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
            self.assertNotIn("orders", tables)

    def test_warehouse_with_sources(self):
        with self.cm.warehouse(with_sources=True) as conn:
            n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            self.assertEqual(n, 3)

    def test_source_scanner_registers_views(self):
        with self.cm.source_scanner() as conn:
            n = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            self.assertEqual(n, 3)

    def test_count_source_rows(self):
        self.assertEqual(self.cm.count_source_rows(), {"orders.csv": 3})

    def test_cache_reused_across_connections(self):
        real_load = ConnectionManager._load_dataframe
        with patch.object(
            ConnectionManager, "_load_dataframe", side_effect=real_load
        ) as spy:
            with self.cm.warehouse(with_sources=True) as conn:
                conn.execute("SELECT COUNT(*) FROM orders").fetchone()
            with self.cm.source_scanner() as conn:
                conn.execute("SELECT COUNT(*) FROM orders").fetchone()
        self.assertEqual(spy.call_count, 1)

    def test_clear_cache(self):
        with self.cm.warehouse(with_sources=True):
            pass
        self.assertEqual(len(self.cm._df_cache), 1)
        self.cm.clear_cache()
        self.assertEqual(len(self.cm._df_cache), 0)


if __name__ == "__main__":
    unittest.main()
