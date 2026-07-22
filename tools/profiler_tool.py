import os
from typing import Type

import polars as pl
from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from .connection_manager import ConnectionManager
from .csv_loader import TypeInspector, SchemaShiftDetector, sanitize_table_name
from .data_source import SourceFile, detect_encoding_bytes
from schemas import ProfileCSVInput

# Shift detection binary-searches with ~11 re-reads of row slices — unacceptable
# over WebHDFS for very large files.
_SHIFT_SCAN_MAX_BYTES = 256 * 1024 * 1024

_INT_TYPES = {
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
}


def _q(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def _map_duckdb_type(duck_type: str) -> str:
    u = duck_type.upper()
    if u in _INT_TYPES:
        return "INTEGER"
    if u in ("FLOAT", "DOUBLE", "REAL") or u.startswith("DECIMAL"):
        return "FLOAT"
    if u == "DATE" or u.startswith("TIMESTAMP"):
        return "DATE"
    return "STRING"


def _encoding_of(ds, sf: SourceFile) -> str:
    if sf.format == "csv":
        return detect_encoding_bytes(ds.read_head(sf.name))
    if sf.format == "json":
        return "utf-8"
    return "n/a"


def _column_stats(conn, table: str, columns: list[str]) -> tuple[list, list]:
    non_nulls = conn.execute(
        f"SELECT {', '.join(f'COUNT({_q(c)})' for c in columns)} FROM {table}"
    ).fetchone()
    uniques = conn.execute(
        f"SELECT {', '.join(f'approx_count_distinct({_q(c)})' for c in columns)} "
        f"FROM {table}"
    ).fetchone()
    # HLL estimates can exceed the true cardinality — never report more uniques
    # than non-null values.
    return list(non_nulls), [min(u, nn) for u, nn in zip(uniques, non_nulls)]


def _detect_shift(conn, table: str, sf: SourceFile, total_rows: int, columns: list) -> dict:
    if sf.size > _SHIFT_SCAN_MAX_BYTES:
        return {"detected": False, "skipped": "file too large for shift scan"}

    def fetch_slice(offset: int, length: int) -> pl.DataFrame:
        return conn.execute(
            f"SELECT * FROM {table} LIMIT {int(length)} OFFSET {int(offset)}"
        ).pl()

    return SchemaShiftDetector.detect(fetch_slice, total_rows, columns)


class ProfileCSVFileTool(BaseTool):
    name: str = "profile_csv_file"
    description: str = "Profiles a dataset file returning row counts, column types, and anomaly signals."
    args_schema: Type[BaseModel] = ProfileCSVInput
    _data_dir: str = PrivateAttr()
    _cm: ConnectionManager = PrivateAttr()

    def __init__(
        self,
        data_dir: str,
        connection_manager: ConnectionManager | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._data_dir = data_dir
        self._cm = connection_manager or ConnectionManager(":memory:", data_dir)

    def _profile(self, file_path: str) -> dict:
        name = os.path.basename(file_path)
        ds = self._cm.data_source
        sf = ds.describe(name)
        table = sanitize_table_name(name)
        with self._cm.source_scanner() as conn:
            desc = conn.execute(f"DESCRIBE {table}").fetchall()
            columns = [r[0] for r in desc]
            duck_types = {r[0]: r[1] for r in desc}
            total_rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            unique_rows = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT * FROM {table})"
            ).fetchone()[0]
            non_nulls, uniques = _column_stats(conn, table, columns)
            sample = conn.execute(f"SELECT * FROM {table} LIMIT 500").pl()
            if all(t.upper() == "VARCHAR" for t in duck_types.values()):
                col_types = TypeInspector.infer(sample)
            else:
                col_types = {
                    c: {"detected_type": _map_duckdb_type(duck_types[c])}
                    for c in columns
                }
            shift_info = _detect_shift(conn, table, sf, total_rows, columns)
        return {
            "encoding": _encoding_of(ds, sf),
            "total_rows": total_rows,
            "duplicates": total_rows - unique_rows,
            "columns": columns,
            "non_nulls": non_nulls,
            "uniques": uniques,
            "col_types": col_types,
            "shift_info": shift_info,
            "sample": sample,
        }

    def _run(self, file_path: str) -> str:
        try:
            p = self._profile(file_path)
        except Exception as e:
            return f"Error profiling dataset: {str(e)}"

        shift_info = p["shift_info"]
        lines = [
            f"Dataset Profile: {file_path}",
            f"Encoding: {p['encoding']}",
            f"Total Rows: {p['total_rows']}",
            f"Total Columns: {len(p['columns'])}",
            f"Duplicate Rows: {p['duplicates']}",
        ]
        if shift_info["detected"]:
            lines.append(
                f"Schema Shift: DETECTED at approximately row {shift_info['approximate_row']}"
            )
            lines.append(
                f"  Affected columns: {[s['column'] for s in shift_info['affected_columns']]}"
            )
            lines.append(f"  Recommendation: {shift_info['recommendation']}")
        elif shift_info.get("skipped"):
            lines.append(f"Schema Shift: NOT SCANNED ({shift_info['skipped']})")
        else:
            lines.append("Schema Shift: NOT DETECTED")

        lines += [
            "",
            "Column Details:",
            f"{'Column':<28} | {'Detected Type':<14} | {'Date Format':<18} | "
            f"{'Non-Null':<9} | {'Nulls':<9} | {'Unique':<10}",
        ]
        for i, col in enumerate(p["columns"]):
            non_null = p["non_nulls"][i]
            null_count = p["total_rows"] - non_null
            type_info = p["col_types"].get(col, {})
            detected = type_info.get("detected_type", "STRING")
            date_fmt = type_info.get("date_format", "")
            lines.append(
                f"{col:<28} | {detected:<14} | {date_fmt:<18} | "
                f"{non_null:<9} | {null_count:<9} | {p['uniques'][i]:<10}"
            )

        lines.append("\nSamples (up to 3 distinct non-null values per column):")
        for col in p["columns"]:
            samples = [
                str(x)
                for x in p["sample"][col].drop_nulls().unique().head(3).to_list()
            ]
            lines.append(f"- {col}: {', '.join(samples)}")

        return "\n".join(lines)

    def profile_as_dict(self, file_path: str) -> dict:
        p = self._profile(file_path)
        total_rows = p["total_rows"]

        col_details = []
        sample_values: dict = {}
        for i, col in enumerate(p["columns"]):
            null_count = int(total_rows - p["non_nulls"][i])
            null_pct = round(null_count / total_rows * 100, 5) if total_rows else 0.0
            type_info = p["col_types"].get(col, {})
            col_details.append(
                {
                    "name": col,
                    "datatype": type_info.get("detected_type", "STRING"),
                    "unique_count": int(p["uniques"][i]),
                    "null_count": null_count,
                    "null_percentage": null_pct,
                }
            )
            samples = [
                str(x)
                for x in p["sample"][col].drop_nulls().unique().head(3).to_list()
            ]
            if samples:
                sample_values[col] = samples

        anomalies = []
        shift_info = p["shift_info"]
        if shift_info["detected"]:
            anomalies.append(
                f"Schema Shift: DETECTED at approximately row {shift_info['approximate_row']}. "
                f"Affected columns: {[s['column'] for s in shift_info['affected_columns']]}"
            )

        return {
            "file_name": file_path,
            "row_count": total_rows,
            "column_count": len(p["columns"]),
            "duplicate_rows": p["duplicates"],
            "encoding": p["encoding"],
            "columns": col_details,
            "anomalies": anomalies,
            "sample_values": sample_values,
        }
