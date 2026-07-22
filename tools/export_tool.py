import os
import uuid
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from .connection_manager import ConnectionManager
from .db_tools import DatabaseService
from schemas import ExportCSVInput


class ExportCSVTool(BaseTool):
    name: str = "export_csv"
    description: str = (
        "Executes a SQL query and saves the FULL result set as a downloadable CSV file "
        "(not truncated like run_duckdb_query). Use when the user asks to export, "
        "download, or save data/results as a file."
    )
    args_schema: Type[BaseModel] = ExportCSVInput
    _data_dir: str = PrivateAttr()
    _exports_dir: str = PrivateAttr()
    _cm: ConnectionManager = PrivateAttr()

    def __init__(
        self,
        data_dir: str,
        exports_dir: str,
        connection_manager: ConnectionManager,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._data_dir = data_dir
        self._exports_dir = exports_dir
        self._cm = connection_manager
        os.makedirs(self._exports_dir, exist_ok=True)

    def _run(self, query: str, label: str) -> str:
        try:
            query = DatabaseService._replace_table_references(
                query, [f.name for f in self._cm.data_source.list_files()]
            )
            with self._cm.warehouse(with_sources=True) as conn:
                df = conn.execute(query).pl()
            if df.is_empty():
                return "Query returned 0 rows — nothing to export."
            filename = f"{uuid.uuid4().hex}.csv"
            df.write_csv(os.path.join(self._exports_dir, filename))
            return f"[Download {label} ({df.height} rows)](/api/exports/{filename})"
        except Exception as e:
            return f"Error exporting CSV: {e}"
