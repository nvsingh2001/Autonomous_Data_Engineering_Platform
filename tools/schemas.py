from pydantic import BaseModel, Field
from typing import List, Optional


class ColumnProfile(BaseModel):
    name: str = Field(description="Column name exactly as it appears in the CSV header")
    datatype: str = Field(
        default="STRING",
        description="Inferred data type: INTEGER, FLOAT, STRING, DATE, BOOLEAN",
    )
    unique_count: int = Field(default=0, description="Number of distinct values")
    null_count: int = Field(default=0, description="Number of null/missing values")
    null_percentage: float = Field(
        default=0.0, description="Percentage of null values (0.0 to 100.0)"
    )


class FileProfile(BaseModel):
    file_name: str = Field(description="Name of the CSV file profiled")
    row_count: int = Field(description="Total number of data rows (excluding header)")
    column_count: int = Field(description="Total number of columns")
    duplicate_rows: int = Field(
        default=0, description="Number of duplicate rows detected"
    )
    encoding: str = Field(default="utf-8", description="File encoding detected")
    columns: List[ColumnProfile] = Field(description="Profile for each column")
    anomalies: List[str] = Field(
        default_factory=list,
        description="Detected anomalies e.g. schema shifts, embedded headers, high nullity",
    )
    sample_values: Optional[dict] = Field(
        default=None,
        description="Optional dict of column_name -> list of sample values",
    )
