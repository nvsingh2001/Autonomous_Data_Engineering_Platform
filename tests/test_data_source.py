import io
import os
import shutil
import tempfile
import unittest

import fsspec
import polars as pl

from tools import ConnectionManager
from tools.data_source import DataSource

_ORDERS_CSV = "order_id,amount\n1,10.00\n2,20.00\n3,30.00\n"


def _parquet_bytes() -> bytes:
    buf = io.BytesIO()
    pl.DataFrame(
        {"item_id": [1, 2], "price": [5.5, 7.25], "sold_on": ["2023-01-01", "2023-01-02"]}
    ).write_parquet(buf)
    return buf.getvalue()


class TestLocalDataSource(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="adep_ds_test_")
        with open(os.path.join(self.temp_dir, "orders.csv"), "w") as f:
            f.write(_ORDERS_CSV)
        with open(os.path.join(self.temp_dir, "items.parquet"), "wb") as f:
            f.write(_parquet_bytes())
        with open(os.path.join(self.temp_dir, "notes.txt"), "w") as f:
            f.write("ignore me")
        self.ds = DataSource(
            fsspec.filesystem("file"), self.temp_dir, native_paths=True
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_files_filters_and_sorts(self):
        files = self.ds.list_files()
        self.assertEqual([f.name for f in files], ["items.parquet", "orders.csv"])
        self.assertEqual([f.format for f in files], ["parquet", "csv"])
        self.assertTrue(all(f.size > 0 for f in files))

    def test_exists_and_describe(self):
        self.assertTrue(self.ds.exists("orders.csv"))
        self.assertFalse(self.ds.exists("missing.csv"))
        self.assertFalse(self.ds.exists("notes.txt"))
        self.assertEqual(self.ds.describe("items.parquet").format, "parquet")

    def test_uri_is_native_path(self):
        self.assertEqual(
            self.ds.uri("orders.csv"), os.path.join(self.temp_dir, "orders.csv")
        )

    def test_read_head(self):
        self.assertTrue(self.ds.read_head("orders.csv").startswith(b"order_id"))

    def test_read_sample_csv_and_parquet(self):
        csv_sample = self.ds.read_sample("orders.csv", n_rows=2)
        self.assertEqual(csv_sample.height, 2)
        self.assertEqual(csv_sample.columns, ["order_id", "amount"])
        pq_sample = self.ds.read_sample("items.parquet", n_rows=5)
        self.assertEqual(pq_sample.height, 2)
        self.assertIn("price", pq_sample.columns)

    def test_write_and_delete(self):
        self.ds.write_bytes("new.csv", b"a,b\n1,2\n")
        self.assertTrue(self.ds.exists("new.csv"))
        self.ds.delete("new.csv")
        self.assertFalse(self.ds.exists("new.csv"))

    def test_missing_dir_lists_empty(self):
        ds = DataSource(
            fsspec.filesystem("file"),
            os.path.join(self.temp_dir, "nope"),
            native_paths=True,
        )
        self.assertEqual(ds.list_files(), [])


class TestRemoteDataSource(unittest.TestCase):
    """memory:// exercises the exact remote code path webhdfs uses: fsspec
    registration on the DuckDB connection plus protocol-prefixed URIs."""

    def setUp(self):
        self.fs = fsspec.filesystem("memory")
        self.fs.pipe("/data/orders.csv", _ORDERS_CSV.encode())
        self.fs.pipe("/data/items.parquet", _parquet_bytes())
        self.ds = DataSource(self.fs, "/data", uri_prefix="memory://")
        self.temp_dir = tempfile.mkdtemp(prefix="adep_ds_remote_")
        self.cm = ConnectionManager(
            os.path.join(self.temp_dir, "warehouse.db"), "unused", data_source=self.ds
        )

    def tearDown(self):
        try:
            self.fs.rm("/data", recursive=True)
        except FileNotFoundError:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_uri_has_protocol(self):
        self.assertEqual(self.ds.uri("orders.csv"), "memory:///data/orders.csv")

    def test_list_and_sample(self):
        self.assertEqual(
            [f.name for f in self.ds.list_files()], ["items.parquet", "orders.csv"]
        )
        sample = self.ds.read_sample("items.parquet", n_rows=1)
        self.assertEqual(sample.height, 1)

    def test_count_source_rows(self):
        self.assertEqual(
            self.cm.count_source_rows(),
            {"items.parquet": 2, "orders.csv": 3},
        )

    def test_csv_view_is_all_varchar(self):
        with self.cm.source_scanner() as conn:
            types = {
                r[0]: r[1] for r in conn.execute("DESCRIBE orders").fetchall()
            }
        self.assertEqual(set(types.values()), {"VARCHAR"})

    def test_parquet_view_keeps_native_types(self):
        with self.cm.source_scanner() as conn:
            types = {
                r[0]: r[1] for r in conn.execute("DESCRIBE items").fetchall()
            }
        self.assertEqual(types["item_id"], "BIGINT")
        self.assertEqual(types["price"], "DOUBLE")

    def test_ctas_from_remote_source(self):
        with self.cm.warehouse(with_sources=True) as conn:
            conn.execute(
                "CREATE TABLE Fact_Orders AS "
                "SELECT CAST(order_id AS BIGINT) AS order_id, "
                "TRY_CAST(amount AS DOUBLE) AS amount FROM orders"
            )
            total = conn.execute("SELECT SUM(amount) FROM Fact_Orders").fetchone()[0]
        self.assertAlmostEqual(total, 60.0)

    def test_write_and_delete_remote(self):
        self.ds.write_bytes("late.csv", b"x\n1\n")
        self.assertTrue(self.ds.exists("late.csv"))
        self.ds.delete("late.csv")
        self.assertFalse(self.ds.exists("late.csv"))


if __name__ == "__main__":
    unittest.main()
