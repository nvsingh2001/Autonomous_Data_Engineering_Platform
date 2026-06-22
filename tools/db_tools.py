from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import duckdb
import polars as pl
import os
import re
import io


class CSVLoader:
    NULL_STRINGS = [
        "NULL", "null", "Null", "NA", "N/A", "n/a", "NaN", "nan", "NAN",
        "None", "none", "NONE", "NIL", "Nil", "nill", "Nill", "NILL",
        "#N/A", "#NULL!", "#VALUE!", "-", "--", "---", "?",
        "undefined", "UNDEFINED", "missing", "MISSING", "n.a.", "N.A.",
    ]

    @staticmethod
    def detect_encoding(file_path: str) -> str:
        try:
            from charset_normalizer import from_path
            result = from_path(file_path, cp_isolation=["utf-8", "latin-1", "cp1252"]).best()
            return str(result.encoding) if result else "utf-8"
        except Exception:
            return "utf-8"

    @classmethod
    def _is_utf8(cls, encoding: str) -> bool:
        return encoding.lower().replace("-", "").replace("_", "") in ("utf8", "ascii", "utf8sig")

    @classmethod
    def read(cls, file_path: str, **kwargs) -> pl.DataFrame:
        encoding = cls.detect_encoding(file_path)
        if cls._is_utf8(encoding):
            return pl.read_csv(file_path, null_values=cls.NULL_STRINGS, infer_schema_length=0, **kwargs)
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return pl.read_csv(
            io.BytesIO(content.encode("utf-8")),
            null_values=cls.NULL_STRINGS,
            infer_schema_length=0,
            **kwargs,
        )

    @classmethod
    def scan(cls, file_path: str) -> tuple:
        encoding = cls.detect_encoding(file_path)
        if cls._is_utf8(encoding):
            return pl.scan_csv(file_path, null_values=cls.NULL_STRINGS, infer_schema_length=0), encoding
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        lf = pl.read_csv(
            io.BytesIO(content.encode("utf-8")),
            null_values=cls.NULL_STRINGS,
            infer_schema_length=0,
        ).lazy()
        return lf, encoding


class TypeInspector:
    DATE_FORMATS = [
        ("%Y-%m-%d",  "ISO"),
        ("%m/%d/%Y",  "US"),
        ("%d/%m/%Y",  "EU"),
        ("%m-%d-%y",  "US-short"),
        ("%d-%m-%Y",  "EU-long"),
        ("%Y/%m/%d",  "ISO-slash"),
        ("%m/%d/%y",  "US-short-slash"),
        ("%d/%m/%y",  "EU-short-slash"),
        ("%Y%m%d",    "compact"),
        ("%d-%b-%Y",  "DD-Mon-YYYY"),
        ("%b %d, %Y", "Mon-DD-YYYY"),
    ]
    _THRESHOLD = 0.80

    @classmethod
    def infer(cls, df_sample: pl.DataFrame) -> dict:
        if df_sample.is_empty():
            return {}
        results = {}
        sample_count = max(len(df_sample), 1)
        conn = duckdb.connect(":memory:")
        conn.register("__sample__", df_sample)
        for col in df_sample.columns:
            qcol = col.replace('"', '""')
            detected = "STRING"
            date_fmt = None
            try:
                n = conn.execute(
                    f'SELECT COUNT(*) FILTER (WHERE TRY_CAST("{qcol}" AS BIGINT) IS NOT NULL'
                    f' AND "{qcol}" != \'\') FROM __sample__'
                ).fetchone()[0]
                if n / sample_count >= cls._THRESHOLD:
                    detected = "INTEGER"
                else:
                    n = conn.execute(
                        f'SELECT COUNT(*) FILTER (WHERE TRY_CAST("{qcol}" AS DOUBLE) IS NOT NULL'
                        f' AND "{qcol}" != \'\') FROM __sample__'
                    ).fetchone()[0]
                    if n / sample_count >= cls._THRESHOLD:
                        detected = "FLOAT"
                    else:
                        for fmt, _ in cls.DATE_FORMATS:
                            n = conn.execute(
                                f"SELECT COUNT(*) FILTER (WHERE TRY_STRPTIME(\"{qcol}\", '{fmt}') IS NOT NULL"
                                f" AND \"{qcol}\" != '') FROM __sample__"
                            ).fetchone()[0]
                            if n / sample_count >= cls._THRESHOLD:
                                detected = "DATE"
                                date_fmt = fmt
                                break
            except Exception:
                pass
            results[col] = {"detected_type": detected}
            if date_fmt:
                results[col]["date_format"] = date_fmt
        conn.close()
        return results


class SchemaShiftDetector:
    _SLICE_SIZE = 300
    _NULL_JUMP_THRESHOLD = 0.50
    _MIN_ROWS = 400
    _BINARY_SEARCH_ITERS = 8

    @classmethod
    def _locate_shift_row(cls, lf: pl.LazyFrame, col: str, total_rows: int) -> int:
        lo, hi = 0, total_rows
        for _ in range(cls._BINARY_SEARCH_ITERS):
            mid = (lo + hi) // 2
            try:
                chunk = lf.slice(mid, min(100, total_rows - mid)).collect(engine="streaming")
                null_rate = chunk[col].null_count() / max(len(chunk), 1)
                if null_rate > 0.5:
                    hi = mid
                else:
                    lo = mid
            except Exception:
                break
        return lo

    @classmethod
    def detect(cls, lf: pl.LazyFrame, total_rows: int, columns: list) -> dict:
        if total_rows < cls._MIN_ROWS:
            return {"detected": False}
        slice_size = min(cls._SLICE_SIZE, total_rows // 6)
        mid_start = total_rows // 2
        try:
            head_df = lf.head(slice_size).collect(engine="streaming")
            mid_df  = lf.slice(mid_start, slice_size).collect(engine="streaming")
            tail_df = lf.tail(slice_size).collect(engine="streaming")
        except Exception:
            return {"detected": False}
        shift_signals = []
        for col in columns:
            try:
                h = head_df[col].null_count() / max(len(head_df), 1)
                m = mid_df[col].null_count()  / max(len(mid_df),  1)
                t = tail_df[col].null_count()  / max(len(tail_df), 1)
                if max(abs(m - h), abs(t - m), abs(t - h)) > cls._NULL_JUMP_THRESHOLD:
                    shift_signals.append({
                        "column": col,
                        "head_null_pct": round(h * 100, 1),
                        "mid_null_pct":  round(m * 100, 1),
                        "tail_null_pct": round(t * 100, 1),
                    })
            except Exception:
                pass
        if shift_signals:
            approx_row = cls._locate_shift_row(lf, shift_signals[0]["column"], total_rows)
            return {
                "detected": True,
                "approximate_row": approx_row,
                "affected_columns": shift_signals,
                "recommendation": (
                    f"Layout changes near row {approx_row}. Use CASE WHEN with row index "
                    "or partition the source table to handle two column layouts."
                ),
            }
        return {"detected": False}


class DatabaseService:
    _SOURCE_EXTENSIONS = (".csv", ".xlsx", ".xls", ".json")

    @staticmethod
    def sanitize_table_name(filename: str) -> str:
        base = os.path.splitext(filename)[0]
        return re.sub(r"[^a-zA-Z0-9_]", "_", base)

    @classmethod
    def _load_dataframe(cls, file_path: str) -> pl.DataFrame:
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

    @classmethod
    def register_sources(cls, conn: duckdb.DuckDBPyConnection, data_dir: str) -> None:
        for filename in os.listdir(data_dir):
            if not filename.endswith(cls._SOURCE_EXTENSIONS):
                continue
            table_name = cls.sanitize_table_name(filename)
            file_path = os.path.join(data_dir, filename)
            df = cls._load_dataframe(file_path)
            df = df.rename({c: c.strip() for c in df.columns})
            conn.register(table_name, df)

    @classmethod
    def count_source_rows(cls, data_dir: str) -> dict:
        counts: dict = {}
        conn = duckdb.connect(":memory:")
        try:
            cls.register_sources(conn, data_dir)
            for filename in os.listdir(data_dir):
                if not filename.endswith(cls._SOURCE_EXTENSIONS):
                    continue
                table_name = cls.sanitize_table_name(filename)
                try:
                    counts[filename] = conn.execute(
                        f"SELECT COUNT(*) FROM {table_name}"
                    ).fetchone()[0]
                except Exception:
                    pass
        finally:
            conn.close()
        return counts

    @classmethod
    def _replace_table_references(cls, stmt: str, data_dir: str) -> str:
        for filename in os.listdir(data_dir):
            if not filename.endswith(cls._SOURCE_EXTENSIONS):
                continue
            table_name = cls.sanitize_table_name(filename)
            base_name = os.path.splitext(filename)[0]
            escaped_fn = re.escape(filename)
            stmt = re.sub(
                rf"(['\"]?)(?:data/)?{escaped_fn}\1",
                table_name,
                stmt,
                flags=re.IGNORECASE,
            )
            parts = re.split(r"[_ ]+", base_name)
            pattern_parts = [re.escape(p) for p in parts if p]
            if pattern_parts:
                pattern = r"\b" + r"[_ ]+".join(pattern_parts) + r"\b"
                # Only match bare identifiers or double-quoted identifiers.
                # Single-quoted forms are SQL string literals (e.g. VALUES ('AMZ', 'Amazon_Sale_Report'))
                # and must not be rewritten — stripping their quotes produces an unquoted column
                # reference that DuckDB cannot resolve without a FROM clause.
                stmt = re.sub(
                    rf'(["]?){pattern}\1',
                    table_name,
                    stmt,
                    flags=re.IGNORECASE,
                )
        return stmt

    @staticmethod
    def split_sql_statements(sql_text: str) -> list:
        statements = []
        current_stmt: list = []
        in_single_quote = False
        in_double_quote = False
        in_single_comment = False
        in_multi_comment = False
        i = 0
        n = len(sql_text)
        while i < n:
            char = sql_text[i]
            if not in_single_quote and not in_double_quote:
                if not in_single_comment and not in_multi_comment:
                    if char == "-" and i + 1 < n and sql_text[i + 1] == "-":
                        in_single_comment = True
                        current_stmt.append(char)
                        i += 1
                        current_stmt.append(sql_text[i])
                        i += 1
                        continue
                    elif char == "/" and i + 1 < n and sql_text[i + 1] == "*":
                        in_multi_comment = True
                        current_stmt.append(char)
                        i += 1
                        current_stmt.append(sql_text[i])
                        i += 1
                        continue
                elif in_single_comment:
                    if char == "\n":
                        in_single_comment = False
                elif in_multi_comment:
                    if char == "*" and i + 1 < n and sql_text[i + 1] == "/":
                        in_multi_comment = False
                        current_stmt.append(char)
                        i += 1
                        current_stmt.append(sql_text[i])
                        i += 1
                        continue
            if not in_single_comment and not in_multi_comment:
                if char == "'" and not in_double_quote:
                    in_single_quote = not in_single_quote
                elif char == '"' and not in_single_quote:
                    in_double_quote = not in_double_quote
            if (
                char == ";"
                and not in_single_quote
                and not in_double_quote
                and not in_single_comment
                and not in_multi_comment
            ):
                stmt = "".join(current_stmt).strip()
                if stmt:
                    statements.append(stmt)
                current_stmt = []
            else:
                current_stmt.append(char)
            i += 1
        final_stmt = "".join(current_stmt).strip()
        if final_stmt:
            statements.append(final_stmt)
        return statements

    @classmethod
    def execute_sql_script(cls, db_path: str, script_path: str, data_dir: str) -> list:
        if not os.path.exists(script_path):
            return []
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        sql_match = re.search(r"```sql(.*?)```", content, re.DOTALL | re.IGNORECASE)
        sql_text = sql_match.group(1) if sql_match else content
        statements = cls.split_sql_statements(sql_text)
        errors = []
        conn = duckdb.connect(database=db_path)
        try:
            cls.register_sources(conn, data_dir)
            succeeded = 0
            for i, stmt in enumerate(statements):
                stmt = cls._replace_table_references(stmt, data_dir)
                try:
                    conn.execute(stmt)
                    succeeded += 1
                    # After a successful CREATE TABLE, verify no STRUCT-type columns were produced.
                    # A bare table-alias expression (SELECT t FROM tbl t) returns the whole row as
                    # a STRUCT, which causes INSERT failures many statements later with an opaque
                    # type-mismatch error.
                    tbl_match = re.search(
                        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                        stmt,
                        re.IGNORECASE,
                    )
                    if tbl_match:
                        tbl = tbl_match.group(1)
                        try:
                            desc = conn.execute(f"DESCRIBE {tbl}").fetchall()
                            struct_cols = [
                                (row[0], row[1])
                                for row in desc
                                if "STRUCT" in str(row[1]).upper() or "MAP" in str(row[1]).upper()
                            ]
                            if struct_cols:
                                col_list = ", ".join(f"{c[0]} ({c[1]})" for c in struct_cols)
                                snippet = stmt[:200] + "..." if len(stmt) > 200 else stmt
                                errors.append({
                                    "statement_index": i + 1,
                                    "sql_snippet": snippet,
                                    "error": (
                                        f"Schema Error: table '{tbl}' has STRUCT-type column(s): {col_list}. "
                                        f"Root cause: the CREATE TABLE used a bare table alias as an expression "
                                        f"(e.g. SELECT t FROM table_name t) or a correlated row-subquery "
                                        f"(e.g. SELECT (SELECT t FROM tbl t WHERE ...)), which DuckDB returns as "
                                        f"a STRUCT. Fix: rewrite to SELECT individual named columns. "
                                        f"All warehouse columns must be scalar types (VARCHAR, BIGINT, FLOAT, DATE)."
                                    ),
                                })
                                print(f"[DatabaseService] Statement {i + 1}: STRUCT column(s) detected in '{tbl}': {col_list}")
                        except Exception:
                            pass
                except Exception as e:
                    snippet = stmt[:200] + "..." if len(stmt) > 200 else stmt
                    errors.append({
                        "statement_index": i + 1,
                        "sql_snippet": snippet,
                        "error": str(e),
                    })
                    print(f"[DatabaseService] Statement {i + 1}/{len(statements)} failed: {e}")
            print(
                f"[DatabaseService] Execution complete: {succeeded} succeeded, "
                f"{len(errors)} failed out of {len(statements)} statements."
            )
        finally:
            conn.close()
        return errors


class SQLQueryInput(BaseModel):
    query: str = Field(..., description="SQL query to execute.")


class RunDuckDBQueryTool(BaseTool):
    name: str = "run_duckdb_query"
    description: str = "Executes a SQL query against local CSV/Excel/JSON files using DuckDB."
    args_schema: Type[BaseModel] = SQLQueryInput
    _data_dir: str = PrivateAttr()
    _db_path: str = PrivateAttr()

    def __init__(self, data_dir: str, db_path: str = ":memory:", **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir
        self._db_path = db_path

    def _run(self, query: str) -> str:
        try:
            conn = duckdb.connect(database=self._db_path)
            DatabaseService.register_sources(conn, self._data_dir)
            query = DatabaseService._replace_table_references(query, self._data_dir)
            df = conn.execute(query).pl()
            conn.close()
            if df.is_empty():
                return "Query returned 0 rows."
            return str(df)
        except Exception as e:
            return f"Error executing DuckDB query: {str(e)}"


class ProfileCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to file.")


class ProfileCSVFileTool(BaseTool):
    name: str = "profile_csv_file"
    description: str = "Profiles a dataset file returning row counts, column types, and anomaly signals."
    args_schema: Type[BaseModel] = ProfileCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load(self, full_path: str) -> tuple:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".csv":
            lf, enc = CSVLoader.scan(full_path)
        elif ext == ".json":
            try:
                lf = pl.scan_ndjson(full_path)
            except Exception:
                lf = pl.read_json(full_path).lazy()
            enc = "utf-8"
        elif ext in (".xlsx", ".xls"):
            lf = pl.read_excel(full_path, engine="calamine").lazy()
            enc = "n/a"
        else:
            raise ValueError(f"Unsupported format: {ext}")
        return lf.rename({c: c.strip() for c in lf.collect_schema().names()}), enc

    def _run(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            lf, encoding = self._load(full_path)

            total_rows  = lf.select(pl.len()).collect(engine="streaming").item()
            unique_rows = lf.unique().select(pl.len()).collect(engine="streaming").item()
            duplicates  = total_rows - unique_rows
            columns     = lf.collect_schema().names()

            exprs = []
            for col in columns:
                exprs.extend([
                    pl.col(col).null_count().alias(f"{col}_nulls"),
                    pl.col(col).n_unique().alias(f"{col}_uniques"),
                ])
            agg_df    = lf.select(exprs).collect(engine="streaming")
            df_sample = lf.head(500).collect(engine="streaming")

            col_types  = TypeInspector.infer(df_sample)
            shift_info = SchemaShiftDetector.detect(lf, total_rows, columns)

            lines = [
                f"Dataset Profile: {file_path}",
                f"Encoding: {encoding}",
                f"Total Rows: {total_rows}",
                f"Total Columns: {len(columns)}",
                f"Duplicate Rows: {duplicates}",
            ]
            if shift_info["detected"]:
                lines.append(
                    f"Schema Shift: DETECTED at approximately row {shift_info['approximate_row']}"
                )
                lines.append(
                    f"  Affected columns: {[s['column'] for s in shift_info['affected_columns']]}"
                )
                lines.append(f"  Recommendation: {shift_info['recommendation']}")
            else:
                lines.append("Schema Shift: NOT DETECTED")

            lines += [
                "",
                "Column Details:",
                f"{'Column':<28} | {'Detected Type':<14} | {'Date Format':<18} | "
                f"{'Non-Null':<9} | {'Nulls':<9} | {'Unique':<10}",
            ]
            for col in columns:
                null_count   = agg_df.get_column(f"{col}_nulls")[0]
                unique_count = agg_df.get_column(f"{col}_uniques")[0]
                non_null     = total_rows - null_count
                type_info    = col_types.get(col, {})
                detected     = type_info.get("detected_type", "STRING")
                date_fmt     = type_info.get("date_format", "")
                lines.append(
                    f"{col:<28} | {detected:<14} | {date_fmt:<18} | "
                    f"{non_null:<9} | {null_count:<9} | {unique_count:<10}"
                )

            lines.append("\nSamples (up to 3 distinct non-null values per column):")
            for col in columns:
                samples = [
                    str(x) for x in df_sample[col].drop_nulls().unique().head(3).to_list()
                ]
                lines.append(f"- {col}: {', '.join(samples)}")

            return "\n".join(lines)
        except Exception as e:
            return f"Error profiling dataset: {str(e)}"


class PreviewCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to dataset file.")
    limit: int = Field(5, description="Number of rows to preview.")


class ReadCSVPreviewTool(BaseTool):
    name: str = "read_csv_preview"
    description: str = "Reads a preview of the first few rows of a dataset file."
    args_schema: Type[BaseModel] = PreviewCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load(self, full_path: str, limit: int) -> pl.DataFrame:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".csv":
            df = CSVLoader.read(full_path, n_rows=limit)
        elif ext in (".xlsx", ".xls"):
            df = pl.read_excel(full_path, engine="calamine").head(limit)
        elif ext == ".json":
            try:
                df = pl.read_ndjson(full_path).head(limit)
            except Exception:
                df = pl.read_json(full_path).head(limit)
        else:
            raise ValueError(f"Unsupported format: {ext}")
        return df.rename({c: c.strip() for c in df.columns})

    def _run(self, file_path: str, limit: int = 5) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            return str(self._load(full_path, limit))
        except Exception as e:
            return f"Error reading dataset preview: {str(e)}"
