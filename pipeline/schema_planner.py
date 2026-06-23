import os
import re
import json
from tools import DatabaseService


class SchemaPlanner:
    def __init__(self, data_dir: str, star_schema: str, source_row_counts: dict):
        self._data_dir = data_dir
        self._star_schema = star_schema
        self._source_row_counts = source_row_counts

    def table_mapping_text(self) -> str:
        return "\n".join(
            f"- '{fn}' is loaded in DuckDB as table/view: '{DatabaseService.sanitize_table_name(fn)}'"
            for fn in os.listdir(self._data_dir)
            if fn.endswith((".csv", ".xlsx", ".xls", ".json"))
        )

    def parse_schema_plan(self, raw: str) -> list[dict]:
        text = raw.strip()
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if m:
            text = m.group(1).strip()
        bracket = text.find("[")
        if bracket > 0:
            text = text[bracket:]
        try:
            plan = json.loads(text)
            if isinstance(plan, list) and plan:
                return self._complete_schema_plan(plan)
        except Exception:
            pass
        print("[Flow] Schema plan JSON parse failed — falling back to star schema extraction.")
        return self._complete_schema_plan(self._extract_plan_from_star_schema())

    def _extract_plan_from_star_schema(self) -> list[dict]:
        names = list(dict.fromkeys(re.findall(r"\b((?:Fact|Dim)_\w+)\b", self._star_schema)))
        dims  = [{"name": n, "type": "dimension", "sources": [], "description": ""} for n in names if n.lower().startswith("dim_")]
        facts = [{"name": n, "type": "fact",      "sources": [], "description": ""} for n in names if n.lower().startswith("fact_")]
        return dims + facts

    def _complete_schema_plan(self, plan: list[dict]) -> list[dict]:
        if not self._source_row_counts:
            return plan

        covered_views: set[str] = set()
        for spec in plan:
            for v in spec.get("sources", []):
                covered_views.add(v.lower())

        for filename, count in sorted(self._source_row_counts.items(), key=lambda x: -x[1]):
            if count < 5000:
                continue
            view = DatabaseService.sanitize_table_name(filename)
            if view.lower() in covered_views:
                continue

            star_match = re.search(
                rf"\b((?:Fact|Dim)_\w+)\b[^#]*{re.escape(view.split('_')[0])}",
                self._star_schema, re.IGNORECASE,
            )
            if star_match:
                table_name = star_match.group(1)
                table_type = "fact" if table_name.lower().startswith("fact_") else "dimension"
            else:
                parts = view.split("_")
                suffix = "_".join(p.capitalize() for p in parts if p not in ("olist", "dataset"))
                table_name = f"Fact_{suffix}"
                table_type = "fact"

            existing_names = {s["name"].lower() for s in plan}
            if table_name.lower() not in existing_names:
                print(f"[Flow] Schema plan incomplete — adding {table_name} for {filename} ({count:,} rows)")
                plan.append({
                    "name": table_name,
                    "type": table_type,
                    "sources": [view],
                    "description": f"Auto-added: {filename} ({count:,} rows)",
                })
                covered_views.add(view.lower())

        dims  = [s for s in plan if s["name"].lower().startswith("dim_")]
        facts = [s for s in plan if s["name"].lower().startswith("fact_")]
        return dims + facts
