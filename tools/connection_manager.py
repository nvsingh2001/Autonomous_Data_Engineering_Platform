import os
from contextlib import contextmanager

import duckdb
import polars as pl

from .csv_loader import CSVLoader, sanitize_table_name

_SOURCE_EXTENSIONS = (".csv", ".xlsx", ".xls", ".json")


class ConnectionManager:
    def __init__(self, db_path: str, data_dir: str):
        self._db_path = db_path
        self._data_dir = data_dir
        self._df_cache: dict[str, pl.DataFrame] = {}

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @contextmanager
    def warehouse(self, with_sources: bool = False, read_only: bool = False):
        """A connection to the warehouse DB. With ``with_sources=True`` the raw source
        files are also registered as views — needed while building or querying tables
        that read from source data (e.g. the CREATE TABLE … AS SELECT statements).
        ``read_only=True`` opens a non-writing handle (e.g. independent verification)."""
        conn = duckdb.connect(self._db_path, read_only=read_only)
        try:
            if with_sources:
                self._register_sources(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def source_scanner(self):
        """An in-memory connection with only the raw source files registered as views
        (no warehouse). Used to inspect or count source data before the warehouse
        exists."""
        conn = duckdb.connect(":memory:")
        try:
            self._register_sources(conn)
            yield conn
        finally:
            conn.close()

    def count_source_rows(self) -> dict:
        """Row count per source file, keyed by filename. Drives the retention audit."""
        counts: dict = {}
        with self.source_scanner() as conn:
            for filename in os.listdir(self._data_dir):
                if not filename.endswith(_SOURCE_EXTENSIONS):
                    continue
                table_name = sanitize_table_name(filename)
                try:
                    counts[filename] = conn.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                except Exception:
                    pass
        return counts

    def clear_cache(self) -> None:
        self._df_cache.clear()

    def _register_sources(self, conn: duckdb.DuckDBPyConnection) -> None:
        for filename in os.listdir(self._data_dir):
            if not filename.endswith(_SOURCE_EXTENSIONS):
                continue
            table_name = sanitize_table_name(filename)
            file_path = os.path.join(self._data_dir, filename)
            if file_path not in self._df_cache:
                df = self._load_dataframe(file_path)
                self._df_cache[file_path] = df.rename(
                    {c: c.strip() for c in df.columns}
                )
            conn.register(table_name, self._df_cache[file_path])

    @staticmethod
    def _load_dataframe(file_path: str) -> pl.DataFrame:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            return CSVLoader.read(file_path)
        if ext in (".xlsx", ".xls"):
            return pl.read_excel(file_path, engine="calamine")
        if ext == ".json":
            try:
                return pl.read_ndjson(file_path)
            except Exception:
                return pl.read_json(file_path)
        raise ValueError(f"Unsupported file format: {ext}")
