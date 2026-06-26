import os
from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
import polars as pl

from .csv_loader import CSVLoader


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
