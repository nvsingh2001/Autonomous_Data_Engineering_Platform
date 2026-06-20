from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import duckdb
import polars as pl
import os
import re


class SQLQueryInput(BaseModel):
    query: str = Field(..., description="SQL query to execute.")


class DatabaseService:
    @staticmethod
    def sanitize_table_name(filename: str) -> str:
        base = os.path.splitext(filename)[0]
        return re.sub(r"[^a-zA-Z0-9_]", "_", base)

    @staticmethod
    def split_sql_statements(sql_text: str) -> list:
        statements = []
        current_stmt = []
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
                    if char == '-' and i + 1 < n and sql_text[i+1] == '-':
                        in_single_comment = True
                        current_stmt.append(char)
                        i += 1
                        current_stmt.append(sql_text[i])
                        i += 1
                        continue
                    elif char == '/' and i + 1 < n and sql_text[i+1] == '*':
                        in_multi_comment = True
                        current_stmt.append(char)
                        i += 1
                        current_stmt.append(sql_text[i])
                        i += 1
                        continue
                elif in_single_comment:
                    if char == '\n':
                        in_single_comment = False
                elif in_multi_comment:
                    if char == '*' and i + 1 < n and sql_text[i+1] == '/':
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
            if char == ';' and not in_single_quote and not in_double_quote and not in_single_comment and not in_multi_comment:
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

    @staticmethod
    def execute_sql_script(db_path: str, script_path: str, data_dir: str) -> list:
        if not os.path.exists(script_path):
            return []
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()
        sql_match = re.search(r"```sql(.*?)```", content, re.DOTALL | re.IGNORECASE)
        sql_text = sql_match.group(1) if sql_match else content
        statements = DatabaseService.split_sql_statements(sql_text)
        errors = []
        conn = duckdb.connect(database=db_path)
        try:
            for filename in os.listdir(data_dir):
                ext = os.path.splitext(filename)[1].lower()
                if ext in [".csv", ".xlsx", ".xls", ".json"]:
                    table_name = DatabaseService.sanitize_table_name(filename)
                    file_path = os.path.join(data_dir, filename)
                    if ext == ".csv":
                        df_tmp = pl.read_csv(file_path, null_values=["Nill", "nill", "NILL", "NA", "NaN"], ignore_errors=True)
                        conn.register(table_name, df_tmp)
                    elif ext in [".xlsx", ".xls"]:
                        df_tmp = pl.read_excel(file_path, engine="calamine")
                        conn.register(table_name, df_tmp)
                    elif ext == ".json":
                        try:
                            df_tmp = pl.read_ndjson(file_path)
                        except Exception:
                            df_tmp = pl.read_json(file_path)
                        conn.register(table_name, df_tmp)
            succeeded = 0
            for i, stmt in enumerate(statements):
                for filename in os.listdir(data_dir):
                    if filename.endswith((".csv", ".xlsx", ".xls", ".json")):
                        table_name = DatabaseService.sanitize_table_name(filename)
                        base_name, _ = os.path.splitext(filename)

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
                            stmt = re.sub(
                                rf"(['\"]?){pattern}\1",
                                table_name,
                                stmt,
                                flags=re.IGNORECASE,
                            )
                try:
                    conn.execute(stmt)
                    succeeded += 1
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
                f"[DatabaseService] Execution complete: {succeeded} succeeded, {len(errors)} failed out of {len(statements)} statements."
            )
        finally:
            conn.close()
        return errors


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
        try:
            conn = duckdb.connect(database=self._db_path)
            for filename in os.listdir(self._data_dir):
                ext = os.path.splitext(filename)[1].lower()
                if ext in [".csv", ".xlsx", ".xls", ".json"]:
                    table_name = DatabaseService.sanitize_table_name(filename)
                    file_path = os.path.join(self._data_dir, filename)
                    if ext == ".csv":
                        df_tmp = pl.read_csv(file_path, null_values=["Nill", "nill", "NILL", "NA", "NaN"], ignore_errors=True)
                        conn.register(table_name, df_tmp)
                    elif ext in [".xlsx", ".xls"]:
                        df_tmp = pl.read_excel(file_path, engine="calamine")
                        conn.register(table_name, df_tmp)
                    elif ext == ".json":
                        try:
                            df_tmp = pl.read_ndjson(file_path)
                        except Exception:
                            df_tmp = pl.read_json(file_path)
                        conn.register(table_name, df_tmp)
                    escaped_fn = re.escape(filename)
                    query = re.sub(
                        rf"(['\"]?)(?:data/)?{escaped_fn}\1",
                        table_name,
                        query,
                        flags=re.IGNORECASE,
                    )

                    base_name, _ = os.path.splitext(filename)
                    parts = re.split(r"[_ ]+", base_name)
                    pattern_parts = [re.escape(p) for p in parts if p]
                    if pattern_parts:
                        pattern = r"\b" + r"[_ ]+".join(pattern_parts) + r"\b"
                        query = re.sub(
                            rf"(['\"]?){pattern}\1",
                            table_name,
                            query,
                            flags=re.IGNORECASE,
                        )
            df = conn.execute(query).pl()
            if df.is_empty():
                return "Query returned 0 rows."
            return str(df)
        except Exception as e:
            return f"Error executing DuckDB query: {str(e)}"


class ProfileCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to file.")


class ProfileCSVFileTool(BaseTool):
    name: str = "profile_csv_file"
    description: str = "Profiles a dataset file (CSV/Excel/JSON) returning row, column counts and details."
    args_schema: Type[BaseModel] = ProfileCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load_dataset(self, full_path: str) -> pl.LazyFrame:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".csv":
            return pl.scan_csv(full_path, null_values=["Nill", "nill", "NILL", "NA", "NaN"], ignore_errors=True)
        elif ext == ".json":
            try:
                return pl.scan_ndjson(full_path)
            except Exception:
                return pl.read_json(full_path).lazy()
        elif ext in [".xlsx", ".xls"]:
            return pl.read_excel(full_path, engine="calamine").lazy()
        else:
            raise ValueError(f"Unsupported format: {ext}")

    def _run(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            lf = self._load_dataset(full_path)
            
            # total rows and duplicates using streaming collect
            total_rows = lf.select(pl.len()).collect(engine="streaming").item()
            unique_rows = lf.unique().select(pl.len()).collect(engine="streaming").item()
            duplicates = total_rows - unique_rows
            
            columns = lf.collect_schema().names()
            schema = lf.collect_schema()
            
            # compute nulls and uniques in one pass
            exprs = []
            for col in columns:
                exprs.extend([
                    pl.col(col).null_count().alias(f"{col}_nulls"),
                    pl.col(col).n_unique().alias(f"{col}_uniques")
                ])
            agg_df = lf.select(exprs).collect(engine="streaming")
            
            # samples from first 100 rows
            df_sample = lf.head(100).collect(engine="streaming")

            report = [
                f"Dataset Profile: {file_path}",
                f"Total Rows: {total_rows}",
                f"Total Columns: {len(columns)}",
                f"Duplicate Rows: {duplicates}",
                "",
                "Column Details:",
                f"{'Column':<25} | {'Dtype':<10} | {'Non-Null':<8} | {'Null Count':<10} | {'Unique':<12}",
            ]
            for col in columns:
                dtype = str(schema[col])
                null_count = agg_df.get_column(f"{col}_nulls")[0]
                non_null = total_rows - null_count
                unique_count = agg_df.get_column(f"{col}_uniques")[0]
                report.append(
                    f"{col:<25} | {dtype:<10} | {non_null:<8} | {null_count:<10} | {unique_count:<12}"
                )

            report.append("\nSamples:")
            for col in columns:
                unique_non_null = df_sample[col].drop_nulls().unique().head(3).to_list()
                samples = [str(x) for x in unique_non_null]
                report.append(f"- {col}: {', '.join(samples)}")
            return "\n".join(report)
        except Exception as e:
            return f"Error profiling dataset: {str(e)}"


class PreviewCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to dataset file.")
    limit: int = Field(5, description="Number of rows to preview.")


class ReadCSVPreviewTool(BaseTool):
    name: str = "read_csv_preview"
    description: str = (
        "Reads a preview of the first few rows of a dataset file (CSV/Excel/JSON)."
    )
    args_schema: Type[BaseModel] = PreviewCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load_dataset(self, full_path: str, limit: int) -> pl.DataFrame:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".csv":
            return pl.read_csv(full_path, n_rows=limit, null_values=["Nill", "nill", "NILL", "NA", "NaN"], ignore_errors=True)
        elif ext in [".xlsx", ".xls"]:
            return pl.read_excel(full_path, engine="calamine").head(limit)
        elif ext == ".json":
            try:
                return pl.read_ndjson(full_path).head(limit)
            except Exception:
                return pl.read_json(full_path).head(limit)
        else:
            raise ValueError(f"Unsupported format: {ext}")

    def _run(self, file_path: str, limit: int = 5) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            df = self._load_dataset(full_path, limit)
            return str(df)
        except Exception as e:
            return f"Error reading dataset preview: {str(e)}"
