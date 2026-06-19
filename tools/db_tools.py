import duckdb
import pandas as pd
from crewai.tools import tool

@tool("run_duckdb_query")
def run_duckdb_query(query: str) -> str:
    """Executes a SQL query against CSV files in 'data' using DuckDB.
    Example: 'SELECT * FROM "data/crm_customers.csv" LIMIT 5'"""
    try:
        conn = duckdb.connect(database=':memory:')
        df = conn.execute(query).df()
        if df.empty:
            return "Query returned 0 rows."
        return df.to_string(index=False)
    except Exception as e:
        return f"Error executing DuckDB query: {str(e)}"

@tool("profile_csv_file")
def profile_csv_file(file_path: str) -> str:
    """Analyzes a CSV file using Pandas and returns a profile report:
    Total rows, columns, duplicate rows count, and data type details."""
    try:
        df = pd.read_csv(file_path)
        total_rows = len(df)
        total_cols = len(df.columns)
        duplicates = df.duplicated().sum()
        
        report = []
        report.append(f"CSV Profile for: {file_path}")
        report.append(f"=================================")
        report.append(f"Total Rows: {total_rows}")
        report.append(f"Total Columns: {total_cols}")
        report.append(f"Duplicate Rows: {duplicates}")
        report.append("")
        report.append("Column Profiles:")
        report.append(f"{'Column Name':<25} | {'Dtype':<10} | {'Non-Null':<8} | {'Null Count':<10} | {'Null %':<8} | {'Unique Count':<12}")
        report.append("-" * 85)
        
        for col in df.columns:
            dtype = str(df[col].dtype)
            non_null = df[col].count()
            null_count = total_rows - non_null
            null_pct = (null_count / total_rows) * 100 if total_rows > 0 else 0
            unique_count = df[col].nunique()
            
            report.append(f"{col:<25} | {dtype:<10} | {non_null:<8} | {null_count:<10} | {null_pct:<7.2f}% | {unique_count:<12}")
            
        report.append("")
        report.append("Column Data Previews (first 3 distinct non-null values):")
        for col in df.columns:
            non_null_vals = df[col].dropna().unique()
            samples = [str(x) for x in non_null_vals[:3]]
            report.append(f"- {col}: {', '.join(samples)}")
            
        return "\n".join(report)
    except Exception as e:
        return f"Error profiling CSV file: {str(e)}"

@tool("read_csv_preview")
def read_csv_preview(file_path: str, limit: int = 5) -> str:
    """Reads the first few rows of a CSV file and returns them as a string."""
    try:
        df = pd.read_csv(file_path, nrows=limit)
        return df.to_string(index=False)
    except Exception as e:
        return f"Error reading CSV preview: {str(e)}"
