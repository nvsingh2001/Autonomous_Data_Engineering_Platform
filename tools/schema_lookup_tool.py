import os

from crewai.tools import BaseTool
from pydantic import PrivateAttr

_MAX_CHARS = 3000


class LookupSchemaTool(BaseTool):
    name: str = "lookup_schema"
    description: str = (
        "Returns the warehouse's star schema design: fact/dimension tables, grain "
        "definitions, and foreign-key relationships. Use before writing SQL that joins "
        "multiple tables, or when asked how the warehouse is structured."
    )
    _schema_path: str = PrivateAttr()

    def __init__(self, schema_path: str, **kwargs):
        super().__init__(**kwargs)
        self._schema_path = schema_path

    def _run(self) -> str:
        if not os.path.exists(self._schema_path):
            return "No schema design document is available for this warehouse."
        with open(self._schema_path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > _MAX_CHARS:
            content = content[:_MAX_CHARS] + "\n... [truncated]"
        return content
