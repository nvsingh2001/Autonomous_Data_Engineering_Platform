import json
import os
from typing import Type
from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from schemas import RecordClaimsInput


class RecordClaimsTool(BaseTool):
    name: str = "record_claims"
    description: str = (
        "Records every quantitative claim just written in a report section, each paired with "
        "the exact SQL query that produced it. Call this once per section, right after writing "
        "it, with every number in that section. This lets the pipeline independently re-run your "
        "SQL and confirm the report matches — call it truthfully, with the SQL you actually ran."
    )
    args_schema: Type[BaseModel] = RecordClaimsInput
    _reports_dir: str = PrivateAttr()

    def __init__(self, reports_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._reports_dir = reports_dir

    def _run(self, claims: list) -> str:
        try:
            path = os.path.join(self._reports_dir, "claims.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                for c in claims:
                    entry = c.model_dump() if hasattr(c, "model_dump") else dict(c)
                    f.write(json.dumps(entry) + "\n")
            return f"Recorded {len(claims)} claim(s)."
        except Exception as e:
            return f"Error recording claims: {str(e)}"
