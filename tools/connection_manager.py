from collections import OrderedDict
from contextlib import contextmanager

import duckdb
import polars as pl

from .csv_loader import sanitize_table_name
from .data_source import DataSource, create_source_view, get_data_source

# Cap on distinct source DataFrames held in memory at once (only formats that
# still need the eager fallback: excel, non-UTF-8 csv). A ConnectionManager
# lives for a whole pipeline run or chat request, so without a bound this cache
# grows for as long as new source files keep getting registered.
_MAX_CACHED_DATAFRAMES = 20

_TEMP_DIR = ".duckdb_tmp"


class ConnectionManager:
    def __init__(
        self,
        db_path: str,
        data_dir: str,
        data_source: DataSource | None = None,
    ):
        self._db_path = db_path
        self._data_dir = data_dir
        self._ds = data_source or get_data_source(data_dir)
        self._df_cache: OrderedDict[str, pl.DataFrame] = OrderedDict()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @property
    def data_source(self) -> DataSource:
        return self._ds

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
            for sf in self._ds.list_files():
                table_name = sanitize_table_name(sf.name)
                try:
                    counts[sf.name] = conn.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                except Exception:
                    pass
        return counts

    def clear_cache(self) -> None:
        self._df_cache.clear()

    def _register_sources(self, conn: duckdb.DuckDBPyConnection) -> None:
        # :memory: connections have no spill space unless temp_directory is set —
        # DISTINCT/CTAS over large remote files would OOM instead of spilling.
        conn.execute(f"SET temp_directory = '{_TEMP_DIR}'")
        if self._ds.needs_registration:
            conn.register_filesystem(self._ds.filesystem)
        for sf in self._ds.list_files():
            create_source_view(conn, self._ds, sf, self._df_cache)
            while len(self._df_cache) > _MAX_CACHED_DATAFRAMES:
                self._df_cache.popitem(last=False)
