import os
import re
import sys
import json
from typing import List
from pydantic import BaseModel
from crewai import Crew
from crewai.flow.flow import Flow, start, listen, router
from tools import ToolRegistry, HumanLoopService, DatabaseService, EntityClassifier
from agents import AgentFactory
from tasks import TaskFactory

from dotenv import load_dotenv

load_dotenv()
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"


class DataEngineeringState(BaseModel):
    data_dir: str = "data"
    reports_dir: str = "reports"
    db_path: str = "data/warehouse.db"
    files: List[str] = []
    profiling_results: str = ""
    quality_report: str = ""
    quality_score: int = 100
    star_schema: str = ""
    clean_sql: str = ""
    kpi_report: str = ""
    final_summary: str = ""
    agent_token_usage: dict[str, dict[str, int]] = {}
    source_row_counts: dict[str, int] = {}
    entity_map: dict[str, str] = {}
    primary_fact_table: str = ""
    verified_metrics: dict = {}


class TokenReporter:
    def __init__(self, usage: dict, reports_dir: str):
        self._usage = usage
        self._reports_dir = reports_dir

    def _totals(self) -> dict:
        return {
            "prompt": sum(m["prompt_tokens"] for m in self._usage.values()),
            "completion": sum(m["completion_tokens"] for m in self._usage.values()),
            "total": sum(m["total_tokens"] for m in self._usage.values()),
            "requests": sum(m["successful_requests"] for m in self._usage.values()),
        }

    def write(self) -> None:
        if not self._usage:
            print("[Flow] No token usage tracked.")
            return
        t = self._totals()
        with open(
            os.path.join(self._reports_dir, "token_usage_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self._usage, f, indent=2)
        rows = [
            "| Agent / Role | Prompt Tokens | Completion Tokens | Total Tokens | Requests |",
            "|---|---|---|---|---|",
        ]
        for role, m in self._usage.items():
            rows.append(
                f"| {role} | {m['prompt_tokens']:,} | {m['completion_tokens']:,} "
                f"| {m['total_tokens']:,} | {m['successful_requests']} |"
            )
        rows.append(
            f"| **TOTAL** | **{t['prompt']:,}** | **{t['completion']:,}** "
            f"| **{t['total']:,}** | **{t['requests']}** |"
        )
        md = "# Pipeline Token Usage Report\n\n## Summary Table\n\n" + "\n".join(rows)
        with open(
            os.path.join(self._reports_dir, "token_usage_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(md)
        print("\n" + "=" * 55)
        print("           TOKEN USAGE SUMMARY")
        print("=" * 55)
        for role, m in self._usage.items():
            print(f"Agent: {role}")
            print(f"  Prompt:     {m['prompt_tokens']:,}")
            print(f"  Completion: {m['completion_tokens']:,}")
            print(f"  Total:      {m['total_tokens']:,}")
            print(f"  Requests:   {m['successful_requests']}")
        print("-" * 55)
        print(f"TOTAL PROMPT:     {t['prompt']:,}")
        print(f"TOTAL COMPLETION: {t['completion']:,}")
        print(f"TOTAL TOKENS:     {t['total']:,}")
        print(f"TOTAL REQUESTS:   {t['requests']}")
        print("=" * 55 + "\n")


class DataEngineeringFlow(Flow[DataEngineeringState]):
    def _build_factory(self) -> AgentFactory:
        registry = ToolRegistry(
            data_dir=self.state.data_dir,
            chroma_db_path=".chroma",
            db_path=self.state.db_path,
        )
        return AgentFactory(
            model_name=os.environ.get("PIPELINE_MODEL", "ollama/gemma4:31b-cloud"),
            base_url=os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434"),
            tool_registry=registry,
            sql_model_name=os.environ.get("SQL_MODEL") or None,
            sql_region=os.environ.get("SQL_AWS_REGION") or None,
        )

    def _track_crew_usage(self, crew: Crew) -> None:
        for agent in crew.agents:
            role = agent.role
            if not hasattr(agent, "llm") or not hasattr(
                agent.llm, "get_token_usage_summary"
            ):
                continue
            try:
                usage = agent.llm.get_token_usage_summary()
                bucket = self.state.agent_token_usage.setdefault(
                    role,
                    {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "successful_requests": 0,
                    },
                )
                bucket["prompt_tokens"] += usage.prompt_tokens
                bucket["completion_tokens"] += usage.completion_tokens
                bucket["total_tokens"] += usage.total_tokens
                bucket["successful_requests"] += usage.successful_requests
            except Exception as e:
                print(f"[Flow] Warning: token tracking failed for '{role}': {e}")

    def _entity_map_text(self) -> str:
        return "\n".join(
            f"  - {fn}: {entity}" for fn, entity in self.state.entity_map.items()
        )

    def _compute_verified_metrics(self) -> dict:
        """Compute core warehouse metrics directly from DuckDB — single source of truth for KPI report."""
        import duckdb as _duckdb
        conn = _duckdb.connect(self.state.db_path)
        try:
            all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
            fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
            dim_tables  = [t for t in all_tables if t.lower().startswith("dim_")]

            result: dict = {
                "primary_fact_table": self.state.primary_fact_table,
                "fact_tables": {},
                "dim_tables":  {},
            }

            REVENUE_KEYS = ["price_usd", "amount", "revenue", "total", "value", "sales", "gross"]

            for ft in fact_tables:
                col_names = [c[0] for c in conn.execute(f"DESCRIBE {ft}").fetchall()]
                n = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
                tbl: dict = {"row_count": n, "columns": col_names}

                # Revenue column: first match by priority list, excluding id/key columns
                rev_col = next(
                    (c for c in col_names
                     if any(k in c.lower() for k in REVENUE_KEYS) and "id" not in c.lower()),
                    None,
                )
                if rev_col:
                    total_rev = conn.execute(f"SELECT COALESCE(SUM({rev_col}), 0) FROM {ft}").fetchone()[0]
                    tbl["revenue_column"] = rev_col
                    tbl["total_revenue"]  = round(float(total_rev), 2)

                # Order ID column: match order_id / orderid / any suffix ending in order_id/orderid
                order_col = next(
                    (c for c in col_names if c.lower().replace("_", "") == "orderid"),
                    next((c for c in col_names if c.lower().replace("_", "").endswith("orderid")), None),
                )
                if order_col:
                    unique_orders = conn.execute(f"SELECT COUNT(DISTINCT {order_col}) FROM {ft}").fetchone()[0]
                    tbl["order_id_column"] = order_col
                    tbl["unique_orders"]   = unique_orders
                    if rev_col and unique_orders > 0:
                        tbl["aov"] = round(tbl["total_revenue"] / unique_orders, 2)

                result["fact_tables"][ft] = tbl

            for dt in dim_tables:
                n = conn.execute(f"SELECT COUNT(*) FROM {dt}").fetchone()[0]
                result["dim_tables"][dt] = {"row_count": n}

            return result
        finally:
            conn.close()

    @start()
    def profile_datasets(self) -> None:
        print("[Flow] Starting data profiling...")
        if os.path.exists(self.state.db_path):
            os.remove(self.state.db_path)

        for report in [
            "profiling_report.json",
            "quality_report.md",
            "schema_design.md",
            "transformations.sql",
            "kpi_report.md",
            "executive_summary.md",
        ]:
            old_path = os.path.join(self.state.reports_dir, report)
            if os.path.exists(old_path):
                os.remove(old_path)

        os.makedirs(self.state.data_dir, exist_ok=True)
        discovered = [
            f
            for f in os.listdir(self.state.data_dir)
            if f.endswith((".csv", ".xlsx", ".xls", ".json"))
        ]
        if not discovered:
            print("[Flow] Error: No datasets found in data directory.")
            sys.exit(1)
        self.state.files = discovered

        def _cols_from_raw(raw_profile: dict, fname: str) -> tuple[list, int]:
            """Fallback column extractor for when Pydantic output is unavailable."""
            expected = ("columns", "row_count", "total_rows", "column_details", "structural_metadata", "files")

            def _unwrap(parsed: dict) -> dict:
                if any(k in parsed for k in expected):
                    return parsed
                for key in (f"data/{fname}", fname):
                    inner = parsed.get(key)
                    if isinstance(inner, dict):
                        if any(k in inner for k in expected):
                            return inner
                        for key2 in (f"data/{fname}", fname):
                            inner2 = inner.get(key2)
                            if isinstance(inner2, dict) and any(k in inner2 for k in expected):
                                return inner2
                return parsed

            profile = _unwrap(raw_profile)

            if "files" in profile and isinstance(profile["files"], list):
                for entry in profile["files"]:
                    if isinstance(entry, dict):
                        cols = entry.get("columns", [])
                        if cols:
                            if isinstance(cols[0], dict):
                                return [c.get("column_name", c.get("name", "")) for c in cols if isinstance(c, dict)], profile.get("row_count", 0)
                            return cols, profile.get("row_count", 0)

            meta = profile.get("structural_metadata")
            if isinstance(meta, list) and meta and isinstance(meta[0], dict):
                names = [c.get("column", c.get("column_name", c.get("name", ""))) for c in meta if isinstance(c, dict)]
                names = [n for n in names if n]
                if names:
                    return names, profile.get("row_count", profile.get("file_footprint", {}).get("row_count", 0))

            raw_cols = profile.get("columns") or list(profile.get("column_details", {}).keys())
            if isinstance(raw_cols, dict):
                return list(raw_cols.keys()), profile.get("row_count", profile.get("total_rows", 0))
            if raw_cols and isinstance(raw_cols[0], dict):
                return [c.get("column_name", c.get("name", "")) for c in raw_cols if isinstance(c, dict)], profile.get("row_count", profile.get("total_rows", 0))
            return raw_cols or [], profile.get("row_count", profile.get("total_rows", 0))

        combined_results = {}
        pydantic_profiles: dict = {}  # filename -> FileProfile

        for filename in self.state.files:
            print(f"[Flow] Profiling file: {filename}...")
            factory = self._build_factory()
            profiler = factory.create_profiler()
            task = TaskFactory({"profiler": profiler}).create_profiling_task()
            crew = Crew(agents=[profiler], tasks=[task], verbose=True)
            result = crew.kickoff(inputs={"files": filename})
            self._track_crew_usage(crew)

            if result.pydantic is not None:
                # Deterministic path — Pydantic model parsed successfully
                pydantic_profiles[filename] = result.pydantic
                combined_results[filename] = result.pydantic.model_dump()
            else:
                # Fallback — parse raw JSON
                try:
                    raw = (
                        result.raw.strip()
                        .removeprefix("```json")
                        .removeprefix("```")
                        .removesuffix("```")
                        .strip()
                    )
                    combined_results[filename] = json.loads(raw)
                except Exception as e:
                    print(f"[Flow] Warning: Failed to parse JSON for {filename}: {e}")
                    combined_results[filename] = {"raw_output": result.raw}

        entity_map: dict = {}
        for filename, raw_profile in combined_results.items():
            if filename in pydantic_profiles:
                fp = pydantic_profiles[filename]
                cols = [c.name for c in fp.columns]
                row_count = fp.row_count
            else:
                cols, row_count = _cols_from_raw(raw_profile, filename)
                if not cols and "raw_output" in raw_profile:
                    import re as _re
                    cols = _re.findall(r'"([^"]+)":\s*\{', raw_profile["raw_output"])

            classification = EntityClassifier.classify(
                cols, row_count=row_count, filename=filename
            )
            entity_map[filename] = classification["entity"].value
            combined_results[filename]["_entity"] = classification["entity"].value
            combined_results[filename]["_entity_confidence"] = classification["confidence"]
            combined_results[filename]["_entity_grain"] = classification["grain"]
            if classification["notes"]:
                combined_results[filename]["_entity_notes"] = classification["notes"]
            print(
                f"[Flow] Entity: {filename} → {classification['entity'].value}"
                f" (conf={classification['confidence']:.2f})"
                f"{' | ' + classification['notes'] if classification['notes'] else ''}"
            )
        self.state.entity_map = entity_map

        self.state.profiling_results = json.dumps(combined_results, indent=2)
        os.makedirs(self.state.reports_dir, exist_ok=True)
        with open(
            os.path.join(self.state.reports_dir, "profiling_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(combined_results, f, indent=2)

    @listen(profile_datasets)
    def assess_quality(self) -> None:
        print("[Flow] Assessing data quality...")
        factory = self._build_factory()
        quality_eng = factory.create_quality_engineer()
        task = TaskFactory({"quality_engineer": quality_eng}).create_quality_task()
        crew = Crew(agents=[quality_eng], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={"profiling_results": self.state.profiling_results}
        )
        self._track_crew_usage(crew)
        self.state.quality_report = result.raw
        with open(
            os.path.join(self.state.reports_dir, "quality_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(result.raw)
        match = re.search(r"Quality\s+Score:\s*(\d+)", result.raw, re.IGNORECASE)
        self.state.quality_score = int(match.group(1)) if match else 75
        print(f"[Flow] Quality score: {self.state.quality_score}/100")

    @router(assess_quality)
    def check_quality_threshold(self) -> str:
        if self.state.quality_score < 60:
            if not sys.stdin.isatty():
                print(
                    f"[Flow] Quality score {self.state.quality_score}/100 is below 60 — "
                    "auto-approving (non-interactive mode)."
                )
            else:
                print("[Flow] Quality below 60 — requesting operator approval...")
                summary = (
                    self.state.quality_report[:500] + "..."
                    if len(self.state.quality_report) > 500
                    else self.state.quality_report
                )
                if not HumanLoopService.request_human_approval(
                    self.state.quality_score, summary
                ):
                    print("[Flow] Pipeline aborted by operator.")
                    sys.exit(1)
        return "proceed_pipeline"

    @listen("proceed_pipeline")
    def design_schema(self) -> None:
        print("[Flow] Designing schema...")
        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        task = TaskFactory(
            {"warehouse_architect": architect}
        ).create_schema_design_task()
        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": self.state.profiling_results,
                "entity_map": self._entity_map_text(),
            }
        )
        self._track_crew_usage(crew)
        self.state.star_schema = result.raw
        with open(
            os.path.join(self.state.reports_dir, "schema_design.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.star_schema)

    @listen(design_schema)
    def plan_transformations(self) -> None:
        print("[Flow] Planning transformations...")

        table_mapping_text = "\n".join(
            f"- '{fn}' is loaded in DuckDB as table/view: '{DatabaseService.sanitize_table_name(fn)}'"
            for fn in os.listdir(self.state.data_dir)
            if fn.endswith((".csv", ".xlsx", ".xls", ".json"))
        )

        print("[Flow] Counting source rows for data retention audit...")
        try:
            self.state.source_row_counts = DatabaseService.count_source_rows(
                self.state.data_dir
            )
            sorted_counts = dict(
                sorted(self.state.source_row_counts.items(), key=lambda x: -x[1])
            )
            print(f"[Flow] Source row counts: {sorted_counts}")
        except Exception as e:
            print(f"[Flow] Warning: Could not count source rows: {e}")

        factory = self._build_factory()
        architect = factory.create_warehouse_architect()
        task = TaskFactory(
            {"warehouse_architect": architect}
        ).create_transformation_task()
        crew = Crew(agents=[architect], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "quality_report": self.state.quality_report,
                "star_schema": self.state.star_schema,
                "table_mapping_text": table_mapping_text,
                "entity_map": self._entity_map_text(),
            }
        )
        self._track_crew_usage(crew)
        self.state.clean_sql = result.raw
        trans_file = os.path.join(self.state.reports_dir, "transformations.sql")
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write(self.state.clean_sql)

        import duckdb

        source_row_counts = self.state.source_row_counts
        max_retries = 4
        for attempt in range(max_retries + 1):
            if os.path.exists(self.state.db_path):
                os.remove(self.state.db_path)
            print(f"[Flow] Executing SQL (attempt {attempt + 1}/{max_retries + 1})...")
            errors = DatabaseService.execute_sql_script(
                self.state.db_path, trans_file, self.state.data_dir
            )

            if not errors:
                fact_count = 0
                try:
                    conn   = duckdb.connect(self.state.db_path)
                    tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
                    fact_tables = [t for t in tables if t.lower().startswith("fact_")]

                    if not fact_tables:
                        errors.append(
                            {
                                "statement_index": 99,
                                "sql_snippet": "fact table verification",
                                "error": (
                                    "No Fact table found after SQL execution. "
                                    "The script must CREATE at least one table whose name starts "
                                    "with 'Fact_' (e.g. Fact_Orders, Fact_Sales, Fact_Sessions). "
                                    "Review the schema design and ensure Fact tables are created."
                                ),
                            }
                        )
                    else:
                        # Pick primary fact table — largest by row count
                        fact_counts: dict[str, int] = {}
                        for ft in fact_tables:
                            try:
                                fact_counts[ft] = conn.execute(
                                    f"SELECT COUNT(*) FROM {ft}"
                                ).fetchone()[0]
                            except Exception:
                                fact_counts[ft] = 0
                        primary_fact = max(fact_counts, key=lambda k: fact_counts[k])
                        fact_count   = fact_counts[primary_fact]
                        self.state.primary_fact_table = primary_fact

                    conn.close()
                except Exception as e:
                    errors.append(
                        {
                            "statement_index": 99,
                            "sql_snippet": "database connection check",
                            "error": f"Failed to connect for validation: {e}",
                        }
                    )

                if fact_count == 0 and not errors:
                    errors.append(
                        {
                            "statement_index": 100,
                            "sql_snippet": f"{self.state.primary_fact_table} row count validation",
                            "error": (
                                f"Logic Alert: {self.state.primary_fact_table} contains 0 records. "
                                "Check date parsing — use try_strptime(col, 'format') with the exact "
                                "format string matching the raw sample values in the profiling report. "
                                "Ensure INSERT statements are not filtered by FK constraints."
                            ),
                        }
                    )

                if not errors and source_row_counts:
                    total_source_rows = sum(source_row_counts.values())
                    # Sum ALL Fact_* tables — each source file may feed a different fact table
                    try:
                        conn2 = duckdb.connect(self.state.db_path)
                        all_tables = [t[0] for t in conn2.execute("SHOW TABLES").fetchall()]
                        all_fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
                        total_fact_rows = sum(
                            conn2.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
                            for ft in all_fact_tables
                        )
                        conn2.close()
                    except Exception:
                        total_fact_rows = fact_count
                    if total_source_rows > 0 and total_fact_rows < total_source_rows * 0.50:
                        retention_pct = total_fact_rows / total_source_rows * 100
                        errors.append(
                            {
                                "statement_index": 102,
                                "sql_snippet": "data retention rate audit",
                                "error": (
                                    f"Data Loss Alert: All Fact tables combined have {total_fact_rows:,} rows "
                                    f"but total source rows are {total_source_rows:,} "
                                    f"(retention: {retention_pct:.1f}%, threshold 50%). "
                                    f"Root cause: FK-membership WHERE filters are discarding valid transactions. "
                                    f"Fix: remove all IN (SELECT ... FROM Dim_*) filters; use LEFT JOIN and NULL for unresolvable FKs. "
                                    f"Source counts: {source_row_counts}."
                                ),
                            }
                        )

                if not errors:
                    print("[Flow] Running database validation agent...")
                    try:
                        val_factory = self._build_factory()
                        val_engineer = val_factory.create_validation_engineer()
                        val_task = TaskFactory(
                            {"validation_engineer": val_engineer}
                        ).create_validation_task()
                        val_crew = Crew(
                            agents=[val_engineer], tasks=[val_task], verbose=True
                        )
                        val_result = val_crew.kickoff(
                            inputs={
                                "source_row_counts":  json.dumps(source_row_counts),
                                "primary_fact_table": self.state.primary_fact_table,
                                "entity_map":         self._entity_map_text(),
                            }
                        )
                        self._track_crew_usage(val_crew)
                        with open(
                            os.path.join(
                                self.state.reports_dir, "validation_report.md"
                            ),
                            "w",
                            encoding="utf-8",
                        ) as f:
                            f.write(val_result.raw)
                        if "Validation Status: FAIL" in val_result.raw:
                            errors.append(
                                {
                                    "statement_index": 101,
                                    "sql_snippet": "validation checks",
                                    "error": f"Validation FAIL:\n\n{val_result.raw}",
                                }
                            )
                    except Exception as e:
                        errors.append(
                            {
                                "statement_index": 101,
                                "sql_snippet": "Validation Agent execution",
                                "error": f"Validation agent failed: {e}",
                            }
                        )

            if not errors:
                print("[Flow] SQL executed and validated successfully.")
                self.state.verified_metrics = self._compute_verified_metrics()
                print(f"[Flow] Verified metrics computed: {list(self.state.verified_metrics.get('fact_tables', {}).keys())}")
                break

            if attempt < max_retries:
                error_report = "\n".join(
                    f"- Statement {e['statement_index']}: {e['error']}\n  SQL: {e['sql_snippet']}"
                    for e in errors
                )
                print(
                    f"[Flow] {len(errors)} error(s) — sending to architect for correction (retry {attempt + 1})..."
                )
                fix_factory = self._build_factory()
                fix_architect = fix_factory.create_warehouse_architect()
                fix_task = TaskFactory(
                    {"warehouse_architect": fix_architect}
                ).create_sql_fix_task()
                fix_crew = Crew(agents=[fix_architect], tasks=[fix_task], verbose=True)
                fix_result = fix_crew.kickoff(
                    inputs={
                        "original_sql":       self.state.clean_sql,
                        "error_report":       error_report,
                        "profiling_results":  self.state.profiling_results,
                        "table_mapping_text": table_mapping_text,
                        "primary_fact_table": self.state.primary_fact_table,
                        "entity_map":         self._entity_map_text(),
                    }
                )
                self._track_crew_usage(fix_crew)
                self.state.clean_sql = fix_result.raw
                with open(trans_file, "w", encoding="utf-8") as f:
                    f.write(self.state.clean_sql)
            else:
                error_report = "\n".join(
                    f"- Statement {e['statement_index']}: {e['error']}\n  SQL: {e['sql_snippet']}"
                    for e in errors
                )
                raise ValueError(
                    f"SQL execution failed after {max_retries} retries:\n{error_report}"
                )

    @listen(plan_transformations)
    def run_analytics(self) -> None:
        print("[Flow] Compiling business insights...")
        factory = self._build_factory()
        analytics = factory.create_analytics_engineer()
        task = TaskFactory(
            {"analytics_engineer": analytics}
        ).create_business_insights_task()
        crew = Crew(agents=[analytics], tasks=[task], verbose=True)
        result = crew.kickoff(inputs={
            "clean_sql": self.state.clean_sql,
            "primary_fact_table": self.state.primary_fact_table,
            "entity_map": self._entity_map_text(),
            "verified_metrics": json.dumps(self.state.verified_metrics, indent=2),
        })
        self._track_crew_usage(crew)
        self.state.kpi_report = result.raw
        with open(
            os.path.join(self.state.reports_dir, "kpi_report.md"), "w", encoding="utf-8"
        ) as f:
            f.write(self.state.kpi_report)

    @listen(run_analytics)
    def compile_final_report(self) -> None:
        print("[Flow] Compiling final executive summaries...")
        factory = self._build_factory()
        lead = factory.create_lead_architect()
        task = TaskFactory({"lead_architect": lead}).create_final_report_task()
        crew = Crew(agents=[lead], tasks=[task], verbose=True)
        result = crew.kickoff(
            inputs={
                "profiling_results": self.state.profiling_results,
                "quality_report": self.state.quality_report,
                "star_schema": self.state.star_schema,
                "clean_sql": self.state.clean_sql,
                "kpi_report": self.state.kpi_report,
            }
        )
        self._track_crew_usage(crew)
        self.state.final_summary = result.raw
        with open(
            os.path.join(self.state.reports_dir, "executive_summary.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(self.state.final_summary)

        TokenReporter(self.state.agent_token_usage, self.state.reports_dir).write()
        print("[Flow] Completed. All reports generated in 'reports/'.")
