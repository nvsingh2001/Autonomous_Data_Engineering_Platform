from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import duckdb
import pandas as pd
import os
import re

class SQLQueryInput(BaseModel):
    query: str = Field(..., description="SQL query to execute.")

class RunDuckDBQueryTool(BaseTool):
    name: str = "run_duckdb_query"
    description: str = "Executes a SQL query against local CSV/Excel/JSON files using DuckDB."
    args_schema: Type[BaseModel] = SQLQueryInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _run(self, query: str) -> str:
        try:
            conn = duckdb.connect(database=':memory:')
            
            for filename in os.listdir(self._data_dir):
                ext = os.path.splitext(filename)[1].lower()
                if ext in [".csv", ".xlsx", ".xls", ".json"]:
                    table_name = os.path.splitext(filename)[0].replace(" ", "_")
                    file_path = os.path.join(self._data_dir, filename)
                    
                    if ext == ".csv":
                        conn.execute(f"CREATE OR REPLACE TEMPORARY VIEW {table_name} AS SELECT * FROM read_csv_auto('{file_path}')")
                    elif ext in [".xlsx", ".xls"]:
                        df_tmp = pd.read_excel(file_path)
                        conn.register(table_name, df_tmp)
                    elif ext == ".json":
                        conn.execute(f"CREATE OR REPLACE TEMPORARY VIEW {table_name} AS SELECT * FROM read_json_auto('{file_path}')")
            
            df = conn.execute(query).df()
            if df.empty:
                return "Query returned 0 rows."
            return df.to_string(index=False)
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

    def _load_dataset(self, full_path: str) -> pd.DataFrame:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == '.csv':
            return pd.read_csv(full_path)
        elif ext in ['.xlsx', '.xls']:
            return pd.read_excel(full_path)
        elif ext == '.json':
            return pd.read_json(full_path)
        else:
            raise ValueError(f"Unsupported format: {ext}")

    def _run(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            df = self._load_dataset(full_path)
            total_rows = len(df)
            total_cols = len(df.columns)
            duplicates = df.duplicated().sum()
            
            report = [
                f"Dataset Profile: {file_path}",
                f"Total Rows: {total_rows}",
                f"Total Columns: {total_cols}",
                f"Duplicate Rows: {duplicates}",
                "",
                "Column Details:",
                f"{'Column':<25} | {'Dtype':<10} | {'Non-Null':<8} | {'Null Count':<10} | {'Unique':<12}"
            ]
            for col in df.columns:
                dtype = str(df[col].dtype)
                non_null = df[col].count()
                null_count = total_rows - non_null
                unique_count = df[col].nunique()
                report.append(f"{col:<25} | {dtype:<10} | {non_null:<8} | {null_count:<10} | {unique_count:<12}")
            
            report.append("\nSamples:")
            for col in df.columns:
                samples = [str(x) for x in df[col].dropna().unique()[:3]]
                report.append(f"- {col}: {', '.join(samples)}")
            return "\n".join(report)
        except Exception as e:
            return f"Error profiling dataset: {str(e)}"

class PreviewCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to dataset file.")
    limit: int = Field(5, description="Number of rows to preview.")

class ReadCSVPreviewTool(BaseTool):
    name: str = "read_csv_preview"
    description: str = "Reads a preview of the first few rows of a dataset file (CSV/Excel/JSON)."
    args_schema: Type[BaseModel] = PreviewCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _load_dataset(self, full_path: str, limit: int) -> pd.DataFrame:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == '.csv':
            return pd.read_csv(full_path, nrows=limit)
        elif ext in ['.xlsx', '.xls']:
            return pd.read_excel(full_path, nrows=limit)
        elif ext == '.json':
            return pd.read_json(full_path, nrows=limit)
        else:
            raise ValueError(f"Unsupported format: {ext}")

    def _run(self, file_path: str, limit: int = 5) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            df = self._load_dataset(full_path, limit)
            return df.to_string(index=False)
        except Exception as e:
            return f"Error reading dataset preview: {str(e)}"
