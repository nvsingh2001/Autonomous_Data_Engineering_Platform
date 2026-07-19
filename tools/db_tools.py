from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr
import os
import re

from .csv_loader import sanitize_table_name
from .connection_manager import ConnectionManager
from schemas import SQLQueryInput
from logging_setup import get_logger

_LOG = get_logger("DatabaseService")


class DatabaseService:
    """Stateless SQL utilities: table-name sanitisation, source-reference rewriting,
    and warehouse script execution. Connection lifecycle and the source-DataFrame cache
    are owned by ConnectionManager — this class only operates on a connection it is
    handed (via a ConnectionManager) or on plain text."""

    _SOURCE_EXTENSIONS = (".csv", ".xlsx", ".xls", ".json")

    @staticmethod
    def sanitize_table_name(filename: str) -> str:
        return sanitize_table_name(filename)

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
    def execute_sql_script(cls, cm: ConnectionManager, script_path: str) -> list:
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
        data_dir = cm.data_dir
        errors = []
        with cm.warehouse(with_sources=True) as conn:
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
                                _LOG.warning(
                                    f"Statement {i + 1}: STRUCT column(s) detected in '{tbl}': {col_list}"
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
                    _LOG.warning(
                        f"Statement {i + 1}/{len(statements)} failed: {e}"
                    )
            _LOG.info(
                f"Execution complete: {succeeded} succeeded, "
                f"{len(errors)} failed out of {len(statements)} statements."
            )
        return errors


class RunDuckDBQueryTool(BaseTool):
    name: str = "run_duckdb_query"
    description: str = (
        "Executes a SQL query against local CSV/Excel/JSON files using DuckDB."
    )
    args_schema: Type[BaseModel] = SQLQueryInput
    _data_dir: str = PrivateAttr()
    _cm: ConnectionManager = PrivateAttr()

    # An agent-written query has no LIMIT enforced anywhere upstream — an unbounded
    # SELECT * on a large fact table would materialize and stringify the whole result,
    # which can OOM-kill the process (a kill that bypasses every try/except in the
    # pipeline, since the OS ends the process directly). Cap both dimensions.
    _MAX_ROWS = 500
    _MAX_CHARS = 20000

    def __init__(
        self,
        data_dir: str,
        db_path: str = ":memory:",
        connection_manager: ConnectionManager | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._data_dir = data_dir
        self._cm = connection_manager or ConnectionManager(db_path, data_dir)

    def _run(self, query: str) -> str:
        try:
            query = DatabaseService._replace_table_references(query, self._data_dir)
            with self._cm.warehouse(with_sources=True) as conn:
                df = conn.execute(query).pl()
            if df.is_empty():
                return "Query returned 0 rows."
            total_rows = df.height
            if total_rows > self._MAX_ROWS:
                df = df.head(self._MAX_ROWS)
            text = str(df)
            if total_rows > self._MAX_ROWS:
                text += (
                    f"\n... truncated: showing {self._MAX_ROWS} of {total_rows} rows. "
                    "Add a LIMIT, GROUP BY, or additional filters to narrow the result."
                )
            if len(text) > self._MAX_CHARS:
                text = (
                    text[: self._MAX_CHARS]
                    + "\n... [output truncated — result too large. Narrow the query.]"
                )
            return text
        except Exception as e:
            return f"Error executing DuckDB query: {str(e)}"
