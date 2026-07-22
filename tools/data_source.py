import io
import os
import posixpath
from collections import OrderedDict
from dataclasses import dataclass

import duckdb
import fsspec
import polars as pl

import config
from .csv_loader import CSVLoader, sanitize_table_name

SOURCE_EXTENSIONS = (".csv", ".xlsx", ".xls", ".json", ".parquet")

_FORMATS = {
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
    ".json": "json",
    ".parquet": "parquet",
}


@dataclass(frozen=True)
class SourceFile:
    name: str
    size: int
    format: str


def source_format(filename: str) -> str | None:
    return _FORMATS.get(os.path.splitext(filename)[1].lower())


def detect_encoding_bytes(head: bytes) -> str:
    try:
        from charset_normalizer import from_bytes

        result = from_bytes(
            head, cp_isolation=["utf-8", "latin-1", "cp1252"]
        ).best()
        return str(result.encoding) if result else "utf-8"
    except Exception:
        return "utf-8"


class DataSource:
    """Filesystem-agnostic access to the source datasets. Method shapes (list /
    describe / read-sample / read-bytes) are kept 1:1 mappable to a future MCP
    server facade."""

    def __init__(
        self,
        fs: fsspec.AbstractFileSystem,
        base_path: str,
        *,
        uri_prefix: str = "",
        native_paths: bool = False,
    ):
        self._fs = fs
        self._base_path = base_path
        self._uri_prefix = uri_prefix
        self._native_paths = native_paths

    @property
    def filesystem(self) -> fsspec.AbstractFileSystem:
        return self._fs

    @property
    def needs_registration(self) -> bool:
        return not self._native_paths

    def _path(self, name: str) -> str:
        if self._native_paths:
            return os.path.join(self._base_path, name)
        return posixpath.join(self._base_path, name)

    def uri(self, name: str) -> str:
        if self._native_paths:
            return self._path(name)
        return f"{self._uri_prefix}{self._path(name)}"

    def list_files(self) -> list[SourceFile]:
        try:
            entries = self._fs.ls(self._base_path, detail=True)
        except FileNotFoundError:
            return []
        files = []
        for e in entries:
            if e.get("type") != "file":
                continue
            name = posixpath.basename(e["name"].rstrip("/"))
            fmt = source_format(name)
            if fmt is None:
                continue
            files.append(SourceFile(name=name, size=int(e.get("size") or 0), format=fmt))
        return sorted(files, key=lambda f: f.name)

    def exists(self, name: str) -> bool:
        return source_format(name) is not None and self._fs.exists(self._path(name))

    def describe(self, name: str) -> SourceFile:
        fmt = source_format(name)
        if fmt is None:
            raise ValueError(f"Unsupported file format: {name}")
        info = self._fs.info(self._path(name))
        return SourceFile(name=name, size=int(info.get("size") or 0), format=fmt)

    def open(self, name: str, mode: str = "rb"):
        return self._fs.open(self._path(name), mode)

    def read_head(self, name: str, n_bytes: int = 65536) -> bytes:
        with self.open(name) as f:
            return f.read(n_bytes)

    def read_sample(self, name: str, n_rows: int = 100) -> pl.DataFrame:
        sf = self.describe(name)
        conn = duckdb.connect(":memory:")
        try:
            if self.needs_registration:
                conn.register_filesystem(self._fs)
            table = create_source_view(conn, self, sf)
            return conn.execute(
                f'SELECT * FROM "{table}" LIMIT {int(n_rows)}'
            ).pl()
        finally:
            conn.close()

    def write_bytes(self, name: str, data: bytes) -> None:
        self._fs.makedirs(self._base_path, exist_ok=True)
        with self._fs.open(self._path(name), "wb") as f:
            f.write(data)

    def delete(self, name: str) -> None:
        self._fs.rm(self._path(name))


def get_data_source(data_dir: str = "data") -> DataSource:
    if config.DATA_SOURCE == "hdfs":
        kwargs: dict = {"host": config.HDFS_HOST, "port": config.HDFS_PORT}
        if config.HDFS_TOKEN:
            kwargs["token"] = config.HDFS_TOKEN
        if config.HDFS_USER:
            kwargs["user"] = config.HDFS_USER
        if config.HDFS_PASSWORD:
            kwargs["password"] = config.HDFS_PASSWORD
        if config.HDFS_USE_HTTPS:
            kwargs["use_https"] = True
        if config.HDFS_DATA_PROXY:
            kwargs["data_proxy"] = config.HDFS_DATA_PROXY
        return DataSource(
            fsspec.filesystem("webhdfs", **kwargs),
            config.HDFS_PATH,
            uri_prefix=f"webhdfs://{config.HDFS_HOST}:{config.HDFS_PORT}",
        )
    return DataSource(fsspec.filesystem("file"), data_dir, native_paths=True)


def _sql_str(value: str) -> str:
    return value.replace("'", "''")


def _reader_sql(ds: DataSource, sf: SourceFile) -> str | None:
    """DuckDB table function scanning the file in place, or None when the format
    needs the eager Polars fallback (excel, non-UTF-8 csv)."""
    uri = _sql_str(ds.uri(sf.name))
    if sf.format == "parquet":
        return f"read_parquet('{uri}')"
    if sf.format == "json":
        return f"read_json_auto('{uri}')"
    if sf.format == "csv":
        encoding = detect_encoding_bytes(ds.read_head(sf.name))
        if CSVLoader._is_utf8(encoding):
            nulls = ", ".join(f"'{_sql_str(s)}'" for s in CSVLoader.NULL_STRINGS)
            # all_varchar keeps sources string-typed — generated CTAS SQL relies
            # on TRY_CAST/TRY_STRPTIME over VARCHAR columns.
            return f"read_csv('{uri}', all_varchar=true, nullstr=[{nulls}])"
    return None


def _load_eager(ds: DataSource, sf: SourceFile) -> pl.DataFrame:
    with ds.open(sf.name) as f:
        raw = f.read()
    if sf.format == "excel":
        return pl.read_excel(io.BytesIO(raw), engine="calamine")
    encoding = detect_encoding_bytes(raw[:65536])
    text = raw.decode(encoding, errors="replace")
    return pl.read_csv(
        io.BytesIO(text.encode("utf-8")),
        null_values=CSVLoader.NULL_STRINGS,
        infer_schema_length=0,
    )


def _stripped_select_list(conn, reader: str) -> str:
    cols = [r[0] for r in conn.execute(f"DESCRIBE SELECT * FROM {reader}").fetchall()]
    if all(c == c.strip() for c in cols):
        return "*"
    return ", ".join(
        f'"{c.replace(chr(34), chr(34) * 2)}" AS "{c.strip().replace(chr(34), chr(34) * 2)}"'
        for c in cols
    )


def create_source_view(
    conn: duckdb.DuckDBPyConnection,
    ds: DataSource,
    sf: SourceFile,
    df_cache: OrderedDict | None = None,
) -> str:
    """Expose one source file to a connection under its sanitized table name —
    a TEMP VIEW scanning the file in place, or a registered eager DataFrame for
    formats DuckDB cannot scan."""
    table = sanitize_table_name(sf.name)
    reader = _reader_sql(ds, sf)
    if reader is None:
        key = ds.uri(sf.name)
        if df_cache is not None and key in df_cache:
            df_cache.move_to_end(key)
            df = df_cache[key]
        else:
            df = _load_eager(ds, sf)
            df = df.rename({c: c.strip() for c in df.columns})
            if df_cache is not None:
                df_cache[key] = df
        conn.register(table, df)
        return table
    select = _stripped_select_list(conn, reader)
    conn.execute(
        f'CREATE OR REPLACE TEMP VIEW "{table}" AS SELECT {select} FROM {reader}'
    )
    return table
