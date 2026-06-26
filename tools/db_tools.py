from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import duckdb
import polars as pl
import os
import re

from .csv_loader import CSVLoader

_DF_CACHE: dict[str, pl.DataFrame] = {}


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
            if file_path not in _DF_CACHE:
                df = cls._load_dataframe(file_path)
                _DF_CACHE[file_path] = df.rename({c: c.strip() for c in df.columns})
            conn.register(table_name, _DF_CACHE[file_path])

    @classmethod
    def clear_source_cache(cls) -> None:
        _DF_CACHE.clear()

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
                rf'(["]?)(?:data/)?{escaped_fn}\1',
                table_name,
                stmt,
                flags=re.IGNORECASE,
            )
            parts = re.split(r"[_ ]+", base_name)
            pattern_parts = [re.escape(p) for p in parts if p]
            if pattern_parts:
                pattern = r"\b" + r"[_ ]+".join(pattern_parts) + r"\b"
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
        sql_text = re.sub(
            r"^[ \t]*```[a-z]*[ \t]*$", "", content, flags=re.MULTILINE
        ).strip()
        if not sql_text:
            sql_text = content
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
                                if "STRUCT" in str(row[1]).upper()
                                or "MAP" in str(row[1]).upper()
                            ]
                            if struct_cols:
                                col_list = ", ".join(
                                    f"{c[0]} ({c[1]})" for c in struct_cols
                                )
                                snippet = (
                                    stmt[:200] + "..." if len(stmt) > 200 else stmt
                                )
                                errors.append(
                                    {
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
                                    }
                                )
                                print(
                                    f"[DatabaseService] Statement {i + 1}: STRUCT column(s) detected in '{tbl}': {col_list}"
                                )
                        except Exception:
                            pass
                except Exception as e:
                    snippet = stmt[:200] + "..." if len(stmt) > 200 else stmt
                    errors.append(
                        {
                            "statement_index": i + 1,
                            "sql_snippet": snippet,
                            "error": str(e),
                        }
                    )
                    print(
                        f"[DatabaseService] Statement {i + 1}/{len(statements)} failed: {e}"
                    )
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
    description: str = (
        "Executes a SQL query against local CSV/Excel/JSON files using DuckDB."
    )
    args_schema: Type[BaseModel] = SQLQueryInput
    _data_dir: str = PrivateAttr()
    _db_path: str = PrivateAttr()

    def __init__(self, data_dir: str, db_path: str = ":memory:", **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir
        self._db_path = db_path

    def _run(self, query: str) -> str:
        conn = None
        try:
            conn = duckdb.connect(database=self._db_path)
            DatabaseService.register_sources(conn, self._data_dir)
            query = DatabaseService._replace_table_references(query, self._data_dir)
            df = conn.execute(query).pl()
            if df.is_empty():
                return "Query returned 0 rows."
            return str(df)
        except Exception as e:
            return f"Error executing DuckDB query: {str(e)}"
        finally:
            if conn is not None:
                conn.close()
