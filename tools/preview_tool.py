import os
from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from .data_source import DataSource, get_data_source
from schemas import PreviewCSVInput


class ReadCSVPreviewTool(BaseTool):
    name: str = "read_csv_preview"
    description: str = "Reads a preview of the first few rows of a dataset file."
    args_schema: Type[BaseModel] = PreviewCSVInput
    _ds: DataSource = PrivateAttr()

    def __init__(
        self, data_dir: str, data_source: DataSource | None = None, **kwargs
    ):
        super().__init__(**kwargs)
        self._ds = data_source or get_data_source(data_dir)

    def _run(self, file_path: str, limit: int = 5) -> str:
        try:
            name = os.path.basename(file_path)
            return str(self._ds.read_sample(name, n_rows=limit))
        except Exception as e:
            return f"Error reading dataset preview: {str(e)}"
