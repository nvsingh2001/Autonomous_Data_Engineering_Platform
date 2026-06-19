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
    description: str = "Executes a SQL query against local CSV files using DuckDB."
    args_schema: Type[BaseModel] = SQLQueryInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _run(self, query: str) -> str:
        try:
            conn = duckdb.connect(database=':memory:')
            
            for filename in os.listdir(self._data_dir):
                if filename.endswith(".csv"):
                    table_name = os.path.splitext(filename)[0]
                    file_path = os.path.join(self._data_dir, filename)
                    conn.execute(f"CREATE OR REPLACE TEMPORARY VIEW {table_name} AS SELECT * FROM read_csv_auto('{file_path}')")
            
            # Resolve raw file name references (e.g. 'crm_customers.csv' -> 'data/crm_customers.csv')
            files = ["crm_customers.csv", "products.csv", "sales_transactions.csv", "support_logs.csv"]
            for f in files:
                file_path = os.path.join(self._data_dir, f)
                query = re.sub(rf"(['\"]?){f}\1", f"'{file_path}'", query, flags=re.IGNORECASE)
                
            df = conn.execute(query).df()
            if df.empty:
                return "Query returned 0 rows."
            return df.to_string(index=False)
        except Exception as e:
            return f"Error executing DuckDB query: {str(e)}"

class ProfileCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to CSV file.")

class ProfileCSVFileTool(BaseTool):
    name: str = "profile_csv_file"
    description: str = "Profiles a CSV file returning row, column count, duplicates and datatypes."
    args_schema: Type[BaseModel] = ProfileCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _run(self, file_path: str) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            df = pd.read_csv(full_path)
            total_rows = len(df)
            total_cols = len(df.columns)
            duplicates = df.duplicated().sum()
            
            report = [
                f"CSV Profile: {file_path}",
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
            return f"Error profiling CSV: {str(e)}"

class PreviewCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to CSV file.")
    limit: int = Field(5, description="Number of rows to preview.")

class ReadCSVPreviewTool(BaseTool):
    name: str = "read_csv_preview"
    description: str = "Reads a preview of the first few rows of a CSV file."
    args_schema: Type[BaseModel] = PreviewCSVInput
    _data_dir: str = PrivateAttr()

    def __init__(self, data_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._data_dir = data_dir

    def _run(self, file_path: str, limit: int = 5) -> str:
        try:
            full_path = os.path.join(self._data_dir, os.path.basename(file_path))
            df = pd.read_csv(full_path, nrows=limit)
            return df.to_string(index=False)
        except Exception as e:
            return f"Error reading CSV preview: {str(e)}"
