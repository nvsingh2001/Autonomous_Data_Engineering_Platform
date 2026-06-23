import duckdb


def compute_verified_metrics(db_path: str, primary_fact_table: str) -> dict:
    """Compute core warehouse metrics directly from DuckDB — single source of truth for KPI report."""
    REVENUE_KEYS = ["price_usd", "amount", "revenue", "total", "value", "sales", "gross"]

    conn = duckdb.connect(db_path)
    try:
        all_tables  = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
        fact_tables = [t for t in all_tables if t.lower().startswith("fact_")]
        dim_tables  = [t for t in all_tables if t.lower().startswith("dim_")]

        result: dict = {
            "primary_fact_table": primary_fact_table,
            "fact_tables": {},
            "dim_tables":  {},
        }

        for ft in fact_tables:
            col_names = [c[0] for c in conn.execute(f"DESCRIBE {ft}").fetchall()]
            n = conn.execute(f"SELECT COUNT(*) FROM {ft}").fetchone()[0]
            tbl: dict = {"row_count": n, "columns": col_names}

            rev_col = next(
                (c for c in col_names if any(k in c.lower() for k in REVENUE_KEYS) and "id" not in c.lower()),
                None,
            )
            if rev_col:
                total_rev = conn.execute(f"SELECT COALESCE(SUM({rev_col}), 0) FROM {ft}").fetchone()[0]
                tbl["revenue_column"] = rev_col
                tbl["total_revenue"]  = round(float(total_rev), 2)

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
