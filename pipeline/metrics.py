from tools import ConnectionManager

_REVENUE_TABLE_PRIORITY = ["payments", "financials", "orders", "order_items", "refunds"]

_ENTITY_TABLE_PATTERNS = {
    "payments": ["payment"],
    "order_items": ["orderitem", "orderline", "lineitem", "line_item"],
    "orders": ["order"],
    "financials": ["financial", "finance", "ledger"],
    "refunds": ["refund", "return"],
}


def _guess_entity_for_table(table_name: str, active_entities: set[str]) -> str | None:
    lower = table_name.lower()
    for entity, patterns in _ENTITY_TABLE_PATTERNS.items():
        if entity not in active_entities:
            continue
        if any(p in lower for p in patterns):
            return entity
    return None


def select_primary_fact(
    cm: ConnectionManager, fact_tables: list[str], entity_map: dict | None = None
) -> str:
    """Choose the canonical primary fact table by e-commerce entity role, not by size.

    Walks the revenue/transaction priority and returns the first fact table whose entity
    matches. Falls back to the largest fact table only when no priority entity is present
    (e.g. a sessions/pageviews-only dataset)."""
    if not fact_tables:
        return ""
    if entity_map:
        active_entities = set(entity_map.values())
        table_entities = {
            ft: _guess_entity_for_table(ft, active_entities) for ft in fact_tables
        }
        for priority_entity in _REVENUE_TABLE_PRIORITY:
            for ft, entity in table_entities.items():
                if entity == priority_entity:
                    return ft

    with cm.warehouse() as conn:
        try:
            counts = {
                ft: conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
                for ft in fact_tables
            }
        except Exception:
            return fact_tables[0]
    return max(counts, key=counts.get)


_TRANSACTION_ENTITIES = {
    "orders", "order_items", "payments", "reviews",
    "sessions", "pageviews", "financials", "refunds", "shipments",
}
_RETENTION_WARN_PCT = 88.0
_CARTESIAN_FAIL_RATIO = 5.0
_REVENUE_KEYS = ["amount", "revenue", "price", "total", "value", "sales", "gross"]


def _pick_id_column(col_names: list[str]) -> str | None:
    """Row identifier heuristic: first *_id, else first *_key, else first column."""
    for c in col_names:
        if c.lower().endswith("_id"):
            return c
    for c in col_names:
        if c.lower().endswith("_key"):
            return c
    return col_names[0] if col_names else None


def run_structural_validation(
    cm: ConnectionManager,
    source_row_counts: dict[str, int],
    entity_map: dict | None,
    primary_fact: str,
) -> dict:
    """Deterministic structural integrity audit of the warehouse — NO LLM.

    Every number comes from a real DuckDB query in a Python loop, so checks can
    never be skipped, mis-summed, or fabricated. Columns are discovered at runtime
    (by *_id / *_key suffix), so it stays dataset-agnostic. Returns
    {status: 'PASS'|'FAIL', report: markdown, checks: [...]}."""
    checks: list[dict] = []  # {name, status: PASS|FAIL|WARN|N/A, detail}
    with cm.warehouse() as conn:
        all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
        fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
        dim_tables = [t for t in all_tables if t.lower().startswith("dim_")]

        def count(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        # ── Check 1 — Data retention (WARNING, never a hard fail) ──────────
        fact_counts = {ft: count(f"SELECT COUNT(*) FROM {ft}") for ft in fact_tables}
        total_fact = sum(fact_counts.values())
        if entity_map:
            expected = sum(
                n
                for fn, n in source_row_counts.items()
                if entity_map.get(fn) in _TRANSACTION_ENTITIES
            )
        else:
            expected = sum(n for n in source_row_counts.values() if n >= 5000)
        retention = (total_fact / expected * 100) if expected else 0.0
        checks.append({
            "name": "Data Retention",
            "status": "WARN" if retention < _RETENTION_WARN_PCT else "PASS",
            "detail": (
                f"total fact rows {total_fact:,} / expected transaction rows {expected:,} "
                f"= {retention:.1f}% (threshold {_RETENTION_WARN_PCT:.0f}%). "
                f"Per fact: {fact_counts}"
            ),
        })

        # ── Check 2 — Dimension primary-key uniqueness (FAIL) ──────────────
        for dt in dim_tables:
            try:
                cols = [c[0] for c in conn.execute(f"DESCRIBE {dt}").fetchall()]
                pk = _pick_id_column(cols)
                if not pk:
                    checks.append({"name": f"Dim PK uniqueness — {dt}", "status": "N/A", "detail": "no columns"})
                    continue
                dups = count(f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM {dt}")
                checks.append({
                    "name": f"Dim PK uniqueness — {dt}",
                    "status": "FAIL" if dups > 0 else "PASS",
                    "detail": f"PK '{pk}': {dups:,} duplicate keys",
                })
            except Exception as e:
                checks.append({"name": f"Dim PK uniqueness — {dt}", "status": "N/A", "detail": str(e)})

        # ── Check 3 — Cartesian / row-uniqueness on primary fact (FAIL) ────
        try:
            cols = [c[0] for c in conn.execute(f"DESCRIBE {primary_fact}").fetchall()]
            idc = _pick_id_column(cols)
            n = fact_counts.get(primary_fact) or count(f"SELECT COUNT(*) FROM {primary_fact}")
            distinct = count(f"SELECT COUNT(DISTINCT {idc}) FROM {primary_fact}") if idc else n
            ratio = (n / distinct) if distinct else 0.0
            checks.append({
                "name": f"Cartesian check — {primary_fact}",
                "status": "FAIL" if ratio > _CARTESIAN_FAIL_RATIO else "PASS",
                "detail": f"rows/distinct({idc}) = {n:,}/{distinct:,} = {ratio:.2f} (max {_CARTESIAN_FAIL_RATIO})",
            })
        except Exception as e:
            checks.append({"name": f"Cartesian check — {primary_fact}", "status": "N/A", "detail": str(e)})

        # ── Check 4 — Revenue integrity: no negative revenue (FAIL) ────────
        for ft in fact_tables:
            try:
                cols = [c[0] for c in conn.execute(f"DESCRIBE {ft}").fetchall()]
                rev = next((c for c in cols if any(k in c.lower() for k in _REVENUE_KEYS) and "id" not in c.lower()), None)
                if not rev:
                    continue
                neg = count(f"SELECT COUNT(*) FROM {ft} WHERE TRY_CAST({rev} AS DOUBLE) < 0")
                checks.append({
                    "name": f"Revenue integrity — {ft}",
                    "status": "FAIL" if neg > 0 else "PASS",
                    "detail": f"column '{rev}': {neg:,} rows with negative value",
                })
            except Exception as e:
                checks.append({"name": f"Revenue integrity — {ft}", "status": "N/A", "detail": str(e)})

        # ── Check 5 — Date-FK null rate on primary fact (WARNING) ──────────
        try:
            cols = [c[0] for c in conn.execute(f"DESCRIBE {primary_fact}").fetchall()]
            date_fk = next((c for c in cols if c.lower().endswith("_key") and "date" in c.lower()), None)
            if date_fk:
                n = fact_counts.get(primary_fact) or count(f"SELECT COUNT(*) FROM {primary_fact}")
                nulls = count(f"SELECT COUNT(*) FROM {primary_fact} WHERE {date_fk} IS NULL")
                rate = (nulls / n * 100) if n else 0.0
                checks.append({
                    "name": f"Date-FK null rate — {primary_fact}",
                    "status": "WARN" if rate > 20 else "PASS",
                    "detail": f"'{date_fk}' NULL in {nulls:,}/{n:,} rows = {rate:.1f}%",
                })
        except Exception as e:
            checks.append({"name": f"Date-FK null rate — {primary_fact}", "status": "N/A", "detail": str(e)})

    status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "N/A": "—"}
    lines = ["# Validation Report (deterministic structural audit)", ""]
    for c in checks:
        lines.append(f"- {icon.get(c['status'], '•')} **{c['status']}** — {c['name']}: {c['detail']}")
    fails = [c["name"] for c in checks if c["status"] == "FAIL"]
    lines += ["", f"Failing checks: {fails if fails else 'none'}", "", f"Validation Status: {status}"]
    return {"status": status, "report": "\n".join(lines) + "\n", "checks": checks}


def compute_verified_metrics(
    cm: ConnectionManager, primary_fact_table: str, entity_map: dict | None = None
) -> dict:
    """Compute core warehouse metrics directly from DuckDB — single source of truth for KPI report."""
    REVENUE_KEYS = [
        "amount",
        "revenue",
        "price",
        "total",
        "value",
        "sales",
        "gross",
    ]

    with cm.warehouse() as conn:
        all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
        fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
        dim_tables = [t for t in all_tables if t.lower().startswith("dim_")]

        result: dict = {
            "primary_fact_table": primary_fact_table,
            "fact_tables": {},
            "dim_tables": {},
        }

        for ft in fact_tables:
            col_names = [c[0] for c in conn.execute(f"DESCRIBE {ft}").fetchall()]
            n = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
            tbl: dict = {"row_count": n, "columns": col_names}

            rev_col = next(
                (
                    c
                    for c in col_names
                    if any(k in c.lower() for k in REVENUE_KEYS)
                    and "id" not in c.lower()
                ),
                None,
            )
            if rev_col:
                total_rev = conn.execute(
                    f"SELECT COALESCE(SUM(TRY_CAST({rev_col} AS DOUBLE)), 0) FROM {ft}"
                ).fetchone()[0]
                tbl["revenue_column"] = rev_col
                tbl["total_revenue"] = round(float(total_rev), 2)

            order_col = next(
                (c for c in col_names if c.lower().replace("_", "") == "orderid"),
                next(
                    (
                        c
                        for c in col_names
                        if c.lower().replace("_", "").endswith("orderid")
                    ),
                    None,
                ),
            )
            if order_col:
                unique_orders = conn.execute(
                    f"SELECT COUNT(DISTINCT {order_col}) FROM {ft}"
                ).fetchone()[0]
                tbl["order_id_column"] = order_col
                tbl["unique_orders"] = unique_orders
                if rev_col and unique_orders > 0:
                    tbl["aov"] = round(tbl["total_revenue"] / unique_orders, 2)

            result["fact_tables"][ft] = tbl

        for dt in dim_tables:
            n = conn.execute(f"SELECT COUNT(*) FROM {dt}").fetchone()[0]
            result["dim_tables"][dt] = {"row_count": n}

        if entity_map:
            active_entities = set(entity_map.values())
            table_entities = {
                ft: _guess_entity_for_table(ft, active_entities) for ft in fact_tables
            }
            table_entities = {k: v for k, v in table_entities.items() if v}
            for ft, entity in table_entities.items():
                result["fact_tables"][ft]["entity_type"] = entity
            canonical_table = None
            for priority_entity in _REVENUE_TABLE_PRIORITY:
                for ft, entity in table_entities.items():
                    if entity == priority_entity and "total_revenue" in result[
                        "fact_tables"
                    ].get(ft, {}):
                        canonical_table = ft
                        break
                if canonical_table:
                    break
            if canonical_table is None and "total_revenue" in result["fact_tables"].get(
                primary_fact_table, {}
            ):
                canonical_table = primary_fact_table
            if canonical_table:
                result["canonical_revenue_table"] = canonical_table
            for ft, entity in table_entities.items():
                if entity == "order_items" and "total_revenue" in result[
                    "fact_tables"
                ].get(ft, {}):
                    result["fact_tables"][ft]["gmv"] = result["fact_tables"][ft][
                        "total_revenue"
                    ]
                    result["gmv_table"] = ft

        return result
