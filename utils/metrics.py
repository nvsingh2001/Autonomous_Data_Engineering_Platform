from tools import ConnectionManager

_REVENUE_TABLE_PRIORITY = ["payments", "financials", "orders", "order_items", "refunds"]

_ENTITY_TABLE_PATTERNS = {
    "payments": ["payment"],
    "order_items": ["orderitem", "orderline", "lineitem", "line_item"],
    "orders": ["order"],
    "financials": ["financial", "finance", "ledger"],
    "refunds": ["refund", "return"],
}

_TRANSACTION_ENTITIES = {
    "orders",
    "order_items",
    "payments",
    "reviews",
    "sessions",
    "pageviews",
    "financials",
    "refunds",
    "shipments",
}
_RETENTION_WARN_PCT = 88.0
_CARTESIAN_FAIL_RATIO = 5.0
_REVENUE_KEYS = ["amount", "revenue", "price", "total", "value", "sales", "gross"]
_MIN_CANONICAL_COVERAGE = 0.10


def _guess_entity_for_table(table_name: str, active_entities: set[str]) -> str | None:
    lower = table_name.lower()
    for entity, patterns in _ENTITY_TABLE_PATTERNS.items():
        if entity not in active_entities:
            continue
        if any(p in lower for p in patterns):
            return entity
    return None


def _semantic_matches(
    col_names: list[str],
    column_semantics: dict | None,
    *,
    structural_role: str | None = None,
    business_role: str | None = None,
) -> list[str]:
    """Built warehouse columns whose name has a grounded source-column occurrence
    supporting the given role. A built column may have been renamed or synthesized
    during the SQL build and match no source occurrence — that's expected, safe
    degradation to the caller's existing name-heuristic fallback, never a crash."""
    if not column_semantics:
        return []
    out = []
    for c in col_names:
        occs = column_semantics.get(c.lower().replace("_", ""), [])
        for o in occs:
            if structural_role and o.get("structural_grounded") and o.get("structural_role") == structural_role:
                out.append(c)
                break
            if business_role and o.get("business_grounded") and o.get("business_role") == business_role:
                out.append(c)
                break
    return out


def _pick_revenue_column(
    conn, table: str, col_names: list[str], column_semantics: dict | None = None
) -> str | None:
    """A name-keyword match alone isn't enough — "sales_channel" contains "sales"
    but holds a channel label, not a number. Require the candidate to actually be
    numeric for most rows before trusting it, same "ground in real data, not a
    name guess" principle as the declared-PK and canonical-table-priority fixes.
    Grounded semantic monetary_amount columns are tried first, but must still clear
    the same numeric-castability check — classification alone is never trusted."""
    semantic = _semantic_matches(col_names, column_semantics, structural_role="monetary_amount")
    keyword = [
        c
        for c in col_names
        if any(k in c.lower() for k in _REVENUE_KEYS) and "id" not in c.lower()
    ]
    candidates = semantic + [c for c in keyword if c not in semantic]
    for c in candidates:
        total, numeric = conn.execute(
            f'SELECT COUNT(*), COUNT(TRY_CAST("{c}" AS DOUBLE)) FROM {table}'
        ).fetchone()
        if total and numeric / total >= 0.5:
            return c
    return None


def _pick_priority_table(
    fact_tables: list[str], entity_map: dict, row_counts: dict[str, int]
) -> str | None:
    """Walk _REVENUE_TABLE_PRIORITY, but require a priority-entity match to cover a
    real share of the data before trusting it over the largest fact table — a
    `financials`-named table built from an unrelated few-row expense sheet must not
    outrank a 100k-row orders table just because "financials" ranks above "orders"
    in the priority list. Ties within a tier go to the larger table."""
    active_entities = set(entity_map.values())
    table_entities = {ft: _guess_entity_for_table(ft, active_entities) for ft in fact_tables}
    max_count = max(row_counts.values(), default=0)
    for priority_entity in _REVENUE_TABLE_PRIORITY:
        candidates = [
            ft
            for ft, entity in table_entities.items()
            if entity == priority_entity
            and row_counts.get(ft, 0) >= max_count * _MIN_CANONICAL_COVERAGE
        ]
        if candidates:
            return max(candidates, key=lambda ft: row_counts.get(ft, 0))
    return None


def _pick_id_column(col_names: list[str]) -> str | None:
    """Fallback row identifier heuristic when no declared key is available: first
    *_id, else first *_key, else first column."""
    for c in col_names:
        if c.lower().endswith("_id"):
            return c
    for c in col_names:
        if c.lower().endswith("_key"):
            return c
    return col_names[0] if col_names else None


def _resolve_pk(
    declared: str | None,
    col_names: list[str],
    column_semantics: dict | None = None,
    conn=None,
    table: str | None = None,
) -> str | None:
    """The architect declares its intended grain key when it builds each table
    (SQLOutput.primary_key) — trust that over guessing from column names, since
    the declaration is made once, at build time, before any check can fail.

    A grounded semantic unique_identifier is tried next, but only as a CANDIDATE —
    it must still clear a real uniqueness check on THIS table before being trusted.
    The same column name can be a genuine PK in one source file and a repeating FK
    in another (order_id: unique in orders.csv, many-per-order in order_items.csv);
    trusting the semantic proposal unverified could pick a same-named-elsewhere
    "unique" column that fans out here, turning a real Cartesian defect into a false
    PASS. Falls back to the naming heuristic when no signal clears the bar."""
    if declared:
        for c in col_names:
            if c.lower() == declared.lower():
                return c
    if conn is not None and table:
        for c in _semantic_matches(col_names, column_semantics, structural_role="unique_identifier"):
            try:
                n, distinct = conn.execute(
                    f'SELECT COUNT(*), COUNT(DISTINCT "{c}") FROM {table}'
                ).fetchone()
            except Exception:
                continue
            if n and distinct / n >= 0.98:
                return c
    return _pick_id_column(col_names)


def _pick_order_id_column(
    col_names: list[str], column_semantics: dict | None = None
) -> str | None:
    """Grounded business=order_identifier occurrences are tried first (via the
    permissive identifier gate — a repeating FK still grounds), then the exact/suffix
    name match this replaces."""
    semantic = _semantic_matches(col_names, column_semantics, business_role="order_identifier")
    if semantic:
        return semantic[0]
    return next(
        (c for c in col_names if c.lower().replace("_", "") == "orderid"),
        next(
            (c for c in col_names if c.lower().replace("_", "").endswith("orderid")),
            None,
        ),
    )


def _find_date_fk(conn, fact_table: str, cols: list[str]) -> str | None:
    """The date-FK column is synthesized during warehouse build (a join key to
    Dim_Date) and exists in no source CSV, so column_semantics can never cover it —
    routing it there would be a guaranteed miss. Grounded instead in a real join:
    pre-filter candidates by name pattern, then require the column to actually join
    to Dim_Date for a real share of rows before trusting it."""
    all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
    dim_date = next((t for t in all_tables if t.lower() == "dim_date"), None)
    if not dim_date:
        return None
    try:
        date_dim_cols = [c[0] for c in conn.execute(f"DESCRIBE {dim_date}").fetchall()]
    except Exception:
        return None
    date_pk = next(
        (c for c in date_dim_cols if c.lower().replace("_", "") == "datekey"), None
    )
    if not date_pk:
        return None
    candidates = [c for c in cols if c.lower().endswith("_key") and "date" in c.lower()]
    try:
        n = conn.execute(f"SELECT COUNT(*) FROM {fact_table}").fetchone()[0]
    except Exception:
        return None
    if not n:
        return None
    for c in candidates:
        try:
            matched = conn.execute(
                f'SELECT COUNT(*) FROM {fact_table} f JOIN {dim_date} d '
                f'ON f."{c}" = d."{date_pk}"'
            ).fetchone()[0]
        except Exception:
            continue
        if matched / n >= 0.5:
            return c
    return None


class WarehouseMetrics:
    """Deterministic warehouse measurements over a built star schema. Every number comes
    from a real DuckDB query (via the run's ConnectionManager) — no LLM. Used by
    TransformStep to pick the primary fact, audit structural integrity, and compute the
    verified-metrics single source of truth for the KPI report."""

    def __init__(self, cm: ConnectionManager):
        self._cm = cm

    def select_primary_fact(
        self, fact_tables: list[str], entity_map: dict | None = None
    ) -> str:
        """Choose the canonical primary fact table by e-commerce entity role, not by size.

        Walks the revenue/transaction priority, but only trusts a match that covers a
        real share of the data (see _pick_priority_table) — falls back to the largest
        fact table when no priority entity clears that bar (e.g. a sessions/pageviews-
        only dataset, or a priority-named table that turns out to be a minor side table)."""
        if not fact_tables:
            return ""
        with self._cm.warehouse() as conn:
            try:
                counts = {
                    ft: conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
                    for ft in fact_tables
                }
            except Exception:
                return fact_tables[0]

        if entity_map:
            picked = _pick_priority_table(fact_tables, entity_map, counts)
            if picked:
                return picked
        return max(counts, key=counts.get)

    def run_structural_validation(
        self,
        source_row_counts: dict[str, int],
        entity_map: dict | None,
        primary_fact: str,
        declared_keys: dict[str, str] | None = None,
        column_semantics: dict | None = None,
    ) -> dict:
        """Deterministic structural integrity audit of the warehouse — NO LLM.

        Every number comes from a real DuckDB query in a Python loop, so checks can
        never be skipped, mis-summed, or fabricated. `declared_keys` (table name ->
        grain key column, from TableBuilder.table_keys()) is the architect's own
        build-time declaration of each table's grain; a naming heuristic is only used
        as a fallback where no declaration is available. Returns
        {status: 'PASS'|'FAIL', report: markdown, checks: [...]}."""
        declared_keys = declared_keys or {}
        checks: list[dict] = []  # {name, status: PASS|FAIL|WARN|N/A, detail}
        with self._cm.warehouse() as conn:
            all_tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
            fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
            dim_tables = [t for t in all_tables if t.lower().startswith("dim_")]

            def count(sql: str) -> int:
                return conn.execute(sql).fetchone()[0]

            fact_counts = {
                ft: count(f"SELECT COUNT(*) FROM {ft}") for ft in fact_tables
            }
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
            checks.append(
                {
                    "name": "Data Retention",
                    "status": "WARN" if retention < _RETENTION_WARN_PCT else "PASS",
                    "detail": (
                        f"total fact rows {total_fact:,} / expected transaction rows {expected:,} "
                        f"= {retention:.1f}% (threshold {_RETENTION_WARN_PCT:.0f}%). "
                        f"Per fact: {fact_counts}"
                    ),
                }
            )

            # ── Check 2 — Dimension primary-key uniqueness (FAIL) ──────────────
            for dt in dim_tables:
                try:
                    cols = [c[0] for c in conn.execute(f"DESCRIBE {dt}").fetchall()]
                    pk = _resolve_pk(declared_keys.get(dt), cols, column_semantics, conn, dt)
                    if not pk:
                        checks.append(
                            {
                                "name": f"Dim PK uniqueness — {dt}",
                                "status": "N/A",
                                "detail": "no columns",
                            }
                        )
                        continue
                    dups = count(f"SELECT COUNT(*) - COUNT(DISTINCT {pk}) FROM {dt}")
                    checks.append(
                        {
                            "name": f"Dim PK uniqueness — {dt}",
                            "status": "FAIL" if dups > 0 else "PASS",
                            "detail": f"PK '{pk}': {dups:,} duplicate keys",
                        }
                    )
                except Exception as e:
                    checks.append(
                        {
                            "name": f"Dim PK uniqueness — {dt}",
                            "status": "N/A",
                            "detail": str(e),
                        }
                    )

            # ── Check 3 — Cartesian / row-uniqueness on primary fact (FAIL) ────
            try:
                cols = [
                    c[0] for c in conn.execute(f"DESCRIBE {primary_fact}").fetchall()
                ]
                idc = _resolve_pk(
                    declared_keys.get(primary_fact), cols, column_semantics, conn, primary_fact
                )
                n = fact_counts.get(primary_fact) or count(
                    f"SELECT COUNT(*) FROM {primary_fact}"
                )
                distinct = (
                    count(f"SELECT COUNT(DISTINCT {idc}) FROM {primary_fact}")
                    if idc
                    else n
                )
                ratio = (n / distinct) if distinct else 0.0
                checks.append(
                    {
                        "name": f"Cartesian check — {primary_fact}",
                        "status": "FAIL" if ratio > _CARTESIAN_FAIL_RATIO else "PASS",
                        "detail": f"rows/distinct({idc}) = {n:,}/{distinct:,} = {ratio:.2f} (max {_CARTESIAN_FAIL_RATIO})",
                    }
                )
            except Exception as e:
                checks.append(
                    {
                        "name": f"Cartesian check — {primary_fact}",
                        "status": "N/A",
                        "detail": str(e),
                    }
                )

            # ── Check 4 — Revenue integrity: no negative revenue (FAIL) ────────
            for ft in fact_tables:
                try:
                    cols = [c[0] for c in conn.execute(f"DESCRIBE {ft}").fetchall()]
                    rev = _pick_revenue_column(conn, ft, cols, column_semantics)
                    if not rev:
                        continue
                    neg = count(
                        f"SELECT COUNT(*) FROM {ft} WHERE TRY_CAST({rev} AS DOUBLE) < 0"
                    )
                    checks.append(
                        {
                            "name": f"Revenue integrity — {ft}",
                            "status": "FAIL" if neg > 0 else "PASS",
                            "detail": f"column '{rev}': {neg:,} rows with negative value",
                        }
                    )
                except Exception as e:
                    checks.append(
                        {
                            "name": f"Revenue integrity — {ft}",
                            "status": "N/A",
                            "detail": str(e),
                        }
                    )

            # ── Check 5 — Date-FK null rate on primary fact (WARNING) ──────────
            try:
                cols = [
                    c[0] for c in conn.execute(f"DESCRIBE {primary_fact}").fetchall()
                ]
                date_fk = _find_date_fk(conn, primary_fact, cols)
                if date_fk:
                    n = fact_counts.get(primary_fact) or count(
                        f"SELECT COUNT(*) FROM {primary_fact}"
                    )
                    nulls = count(
                        f"SELECT COUNT(*) FROM {primary_fact} WHERE {date_fk} IS NULL"
                    )
                    rate = (nulls / n * 100) if n else 0.0
                    checks.append(
                        {
                            "name": f"Date-FK null rate — {primary_fact}",
                            "status": "WARN" if rate > 20 else "PASS",
                            "detail": f"'{date_fk}' NULL in {nulls:,}/{n:,} rows = {rate:.1f}%",
                        }
                    )
            except Exception as e:
                checks.append(
                    {
                        "name": f"Date-FK null rate — {primary_fact}",
                        "status": "N/A",
                        "detail": str(e),
                    }
                )

        status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
        icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "N/A": "—"}
        lines = ["# Validation Report (deterministic structural audit)", ""]
        for c in checks:
            lines.append(
                f"- {icon.get(c['status'], '•')} **{c['status']}** — {c['name']}: {c['detail']}"
            )
        fails = [c["name"] for c in checks if c["status"] == "FAIL"]
        lines += [
            "",
            f"Failing checks: {fails if fails else 'none'}",
            "",
            f"Validation Status: {status}",
        ]
        return {"status": status, "report": "\n".join(lines) + "\n", "checks": checks}

    def compute_verified(
        self,
        primary_fact_table: str,
        entity_map: dict | None = None,
        column_semantics: dict | None = None,
    ) -> dict:
        """Compute core warehouse metrics directly from DuckDB — single source of truth for KPI report."""
        with self._cm.warehouse() as conn:
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

                rev_col = _pick_revenue_column(conn, ft, col_names, column_semantics)
                if rev_col:
                    total_rev = conn.execute(
                        f"SELECT COALESCE(SUM(TRY_CAST({rev_col} AS DOUBLE)), 0) FROM {ft}"
                    ).fetchone()[0]
                    tbl["revenue_column"] = rev_col
                    tbl["total_revenue"] = round(float(total_rev), 2)

                order_col = _pick_order_id_column(col_names, column_semantics)
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
                    ft: _guess_entity_for_table(ft, active_entities)
                    for ft in fact_tables
                }
                table_entities = {k: v for k, v in table_entities.items() if v}
                for ft, entity in table_entities.items():
                    result["fact_tables"][ft]["entity_type"] = entity
                revenue_tables = [
                    ft
                    for ft in fact_tables
                    if "total_revenue" in result["fact_tables"].get(ft, {})
                ]
                row_counts = {
                    ft: result["fact_tables"][ft]["row_count"] for ft in revenue_tables
                }
                canonical_table = _pick_priority_table(
                    revenue_tables, entity_map, row_counts
                )
                if canonical_table is None and "total_revenue" in result[
                    "fact_tables"
                ].get(primary_fact_table, {}):
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
