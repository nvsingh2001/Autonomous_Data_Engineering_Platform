import os

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"  # must be set before crewai is imported
import re
import json
from crewai import Crew
from tools import DatabaseService, ConnectionManager
from tasks import TaskFactory


class TableBuilder:
    """Builds warehouse tables one at a time: generate SQL → execute → verify → fix (up to 3 retries)."""

    MAX_RETRIES = 3

    def __init__(
        self,
        cm: ConnectionManager,
        reports_dir: str,
        profiling_results: str,
        star_schema: str,
        build_factory_fn,
        track_usage_fn,
    ):
        self._cm = cm
        self._db_path = cm.db_path
        self._reports_dir = reports_dir
        self._profiling_results = profiling_results
        self._star_schema = star_schema
        self._build_factory = build_factory_fn
        self._track_usage = track_usage_fn
        try:
            self._profiling_data: dict = json.loads(profiling_results)
        except Exception:
            self._profiling_data = {}

    def source_columns_text(self, source_views: list[str]) -> str:
        if not source_views:
            return "(generated table — no source file)"
        profiling = self._profiling_data
        if not profiling:
            return "(profiling data unavailable)"
        parts = []
        for view in source_views:
            matched = False
            for filename, info in profiling.items():
                if (
                    DatabaseService.sanitize_table_name(filename).lower()
                    == view.lower()
                ):
                    cols = info.get("columns", [])
                    if isinstance(cols, list):
                        lines = [
                            f"    {c['name']} ({c.get('datatype', '?')})" for c in cols
                        ]
                        sample = info.get("sample_values", {})
                        sample_str = (
                            "\n    Sample values: "
                            + "; ".join(
                                f"{k}: {v}" for k, v in list(sample.items())[:3]
                            )
                            if sample
                            else ""
                        )
                        parts.append(f"  [{view}]\n" + "\n".join(lines) + sample_str)
                    matched = True
                    break
            if not matched:
                parts.append(f"  [{view}]: (no profiling data found)")
        return "\n".join(parts)

    def existing_tables_text(self, created: list[str]) -> str:
        if not created:
            return "(none — this is the first table)"
        try:
            with self._cm.warehouse() as conn:
                lines = []
                for t in created:
                    try:
                        cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        cols = [r[0] for r in conn.execute(f"DESCRIBE {t}").fetchall()]
                        lines.append(f"  {t}: {cnt:,} rows — columns: {cols}")
                    except Exception:
                        lines.append(f"  {t}: (exists)")
            return "\n".join(lines)
        except Exception:
            return "\n".join(f"  {t}" for t in created)

    def enrich_error(self, errors: list[dict], source_views: list[str]) -> str:
        enriched = []
        for e in errors:
            msg = e["error"]
            sql = e.get("sql_snippet", "")

            if (
                "same number of result columns" in msg
                or "Set operations can only apply" in msg
            ):
                branches = re.split(r"\bUNION\s+(?:ALL\s+)?", sql, flags=re.IGNORECASE)
                counts = []
                for b in branches:
                    sel = re.search(
                        r"SELECT\s+([\s\S]+?)(?:\bFROM\b|\Z)", b.strip(), re.IGNORECASE
                    )
                    if sel:
                        s, depth, n = sel.group(1), 0, 1
                        for ch in s:
                            if ch == "(":
                                depth += 1
                            elif ch == ")":
                                depth -= 1
                            elif ch == "," and depth == 0:
                                n += 1
                        counts.append(n)
                if len(counts) >= 2:
                    msg += (
                        f"\n  Diagnostic: UNION branch column counts = {counts}. "
                        "All branches must match. Add NULL AS col_name to branches with fewer columns."
                    )

            elif "not found in FROM clause" in msg or "Referenced column" in msg:
                col_m = re.search(r'"([^"]+)"', msg)
                col = col_m.group(1) if col_m else "unknown"
                avail = self.source_columns_text(source_views)
                msg += f"\n  Diagnostic: '{col}' not found. Available source columns:\n{avail}"

            elif "does not exist" in msg.lower() and "table" in msg.lower():
                try:
                    with self._cm.warehouse() as conn:
                        existing = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
                    msg += f"\n  Diagnostic: Tables currently in warehouse: {existing}"
                except Exception:
                    pass

            enriched.append(
                f"Statement {e['statement_index']}: {msg}\n  SQL snippet: {sql[:300]}"
            )
        return "\n".join(enriched)

    def run_retention_check(self, source_row_counts: dict[str, int]) -> list[dict]:
        if not source_row_counts:
            return []
        try:
            with self._cm.warehouse() as conn:
                all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
                fact_counts = {
                    t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in all_tables
                    if t.lower().startswith("fact_")
                }
                dim_counts = {
                    t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in all_tables
                    if t.lower().startswith("dim_")
                }
        except Exception as ex:
            print(f"[Flow] Warning: retention check DB error: {ex}")
            return []

        total_src = sum(source_row_counts.values())
        total_dim = sum(dim_counts.values())
        total_fact = sum(fact_counts.values())
        expected = max(total_src - total_dim, 1)

        if total_fact >= expected * 0.70:
            return []

        pct = total_fact / expected * 100
        wh_counts = list(fact_counts.values()) + list(dim_counts.values())
        missing = [
            f"  {fn}: {cnt:,} rows — no warehouse table has ~{cnt:,} rows"
            for fn, cnt in sorted(source_row_counts.items(), key=lambda x: -x[1])
            if cnt >= 5000
            and not any(abs(cnt - wc) / max(cnt, 1) < 0.10 for wc in wh_counts)
        ]
        fact_lines = (
            "\n".join(
                f"  {t}: {c:,}"
                for t, c in sorted(fact_counts.items(), key=lambda x: -x[1])
            )
            or "  (none)"
        )
        dim_lines = (
            "\n".join(
                f"  {t}: {c:,}"
                for t, c in sorted(dim_counts.items(), key=lambda x: -x[1])
            )
            or "  (none)"
        )
        missing_msg = (
            ("MISSING source files (no warehouse table):\n" + "\n".join(missing) + "\n")
            if missing
            else ""
        )
        return [
            {
                "statement_index": 102,
                "sql_snippet": "data retention audit",
                "error": (
                    f"Data Loss Alert: Fact tables contain {total_fact:,} rows but {expected:,} expected "
                    f"(total source {total_src:,} − dim {total_dim:,} = {expected:,}; retention {pct:.1f}%, threshold 70%).\n"
                    f"{missing_msg}"
                    f"Current Fact tables:\n{fact_lines}\n"
                    f"Current Dim tables:\n{dim_lines}\n"
                    "Every source file >5 000 rows needs its own Fact_ or Dim_ table with the same row count."
                ),
            }
        ]

    def build_all(
        self, schema_plan: list[dict], table_mapping: str
    ) -> tuple[list[str], str]:
        """Build all tables in order. Returns (created_tables, combined_sql).

        The caller chooses the primary fact table by entity role (see
        metrics.select_primary_fact); this builder no longer picks one by size."""
        if os.path.exists(self._db_path):
            os.remove(self._db_path)

        created_tables: list[str] = []
        all_sql_parts: list[str] = []
        trans_file = os.path.join(self._reports_dir, "transformations.sql")

        for spec in schema_plan:
            table_name = spec["name"]
            table_type = spec.get("type", "fact")
            source_views = spec.get("sources", [])
            description = spec.get("description", "")

            print(f"[Flow] Building {table_name}...")
            src_cols_text = self.source_columns_text(source_views)
            table_sql: str = ""
            last_error: str = ""
            factory = self._build_factory()

            for attempt in range(self.MAX_RETRIES + 1):
                architect = factory.create_warehouse_architect()
                tf = TaskFactory({"warehouse_architect": architect})

                if attempt == 0:
                    task_obj = tf.create_generate_table_sql_task()
                    inputs = {
                        "table_name": table_name,
                        "table_type": table_type,
                        "source_views": ", ".join(source_views)
                        if source_views
                        else "generated (no source file)",
                        "table_description": description,
                        "source_columns": src_cols_text,
                        "existing_tables": self.existing_tables_text(created_tables),
                        "star_schema": self._star_schema,
                        "table_mapping_text": table_mapping,
                    }
                else:
                    task_obj = tf.create_fix_table_sql_task()
                    inputs = {
                        "table_name": table_name,
                        "failed_sql": table_sql,
                        "error_message": last_error,
                        "source_views": ", ".join(source_views)
                        if source_views
                        else "generated",
                        "source_columns": src_cols_text,
                        "existing_tables": self.existing_tables_text(created_tables),
                        "star_schema": self._star_schema,
                        "table_mapping_text": table_mapping,
                    }

                crew = Crew(agents=[architect], tasks=[task_obj], verbose=True)
                result = crew.kickoff(inputs=inputs)
                self._track_usage(crew)
                table_sql = result.pydantic.sql if result.pydantic else result.raw

                tmp_path = os.path.join(self._reports_dir, f"_tmp_{table_name}.sql")
                with open(tmp_path, "w", encoding="utf-8") as fh:
                    fh.write(table_sql)
                exec_errors = DatabaseService.execute_sql_script(self._cm, tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

                if exec_errors:
                    last_error = self.enrich_error(exec_errors, source_views)
                    print(
                        f"[Flow] {table_name} attempt {attempt + 1} failed — {exec_errors[0]['error'][:80]}..."
                    )
                    if attempt == self.MAX_RETRIES:
                        print(
                            f"[Flow] Warning: {table_name} failed after {self.MAX_RETRIES} retries — skipping."
                        )
                    continue

                try:
                    with self._cm.warehouse() as conn:
                        tables_now = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
                        if table_name not in tables_now:
                            last_error = f"Table {table_name} not found in DB after execution. Tables present: {tables_now}"
                            print(
                                f"[Flow] {table_name} not in DB after attempt {attempt + 1}."
                            )
                            if attempt == self.MAX_RETRIES:
                                print(
                                    f"[Flow] Warning: {table_name} never appeared — skipping."
                                )
                            continue
                        row_count = conn.execute(
                            f"SELECT COUNT(*) FROM {table_name}"
                        ).fetchone()[0]
                except Exception as ex:
                    last_error = f"Verification error: {ex}"
                    if attempt == self.MAX_RETRIES:
                        print(
                            f"[Flow] Warning: {table_name} verification failed — skipping."
                        )
                    continue

                if table_type == "fact" and row_count == 0:
                    last_error = (
                        f"Logic Alert: {table_name} was created but has 0 rows. "
                        "Remove any IN (SELECT … FROM Dim_*) filters; use try_strptime for date parsing."
                    )
                    print(f"[Flow] {table_name}: 0 rows on attempt {attempt + 1}.")
                    if attempt == self.MAX_RETRIES:
                        print(f"[Flow] Warning: {table_name} remains empty — skipping.")
                    continue

                print(f"[Flow] {table_name}: {row_count:,} rows ✓")
                created_tables.append(table_name)
                all_sql_parts.append(f"-- {table_name}\n{table_sql}")
                break

        combined_sql = "\n\n".join(all_sql_parts)
        with open(trans_file, "w", encoding="utf-8") as fh:
            fh.write(combined_sql)

        return created_tables, combined_sql
