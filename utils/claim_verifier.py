import datetime
import re

_ABS_TOLERANCE = 0.01
_REL_TOLERANCE = 0.005


def _to_float(x):
    # DuckDB returns date-minus-date (e.g. AVG(delivered_at - estimated_at), common in
    # delivery-latency claims) as a Python timedelta — float() on it raises TypeError,
    # which would silently drop the cell from comparison and produce a false DIVERGENT
    # on an otherwise-correct claim. Compare in the same "days" unit the SQL implies.
    if isinstance(x, datetime.timedelta):
        return x.total_seconds() / 86400.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _extract_target_numbers(reported_value) -> list[float]:
    """Pull every plausible number out of a reported-value string like '21,092,805.00'
    or '100%' — commas/currency symbols stripped, a percent value compared as its own
    number (e.g. 100 for '100%'), not converted to a fraction."""
    cleaned = re.sub(r"[,%$₹\s]", "", str(reported_value))
    return [
        float(m)
        for m in re.findall(r"-?\d+\.?\d*", cleaned)
        if m and m not in ("-", ".")
    ]


class ClaimVerifier:
    """Re-executes an agent-cited SQL query for a self-recorded claim and checks whether
    the claimed number actually appears in the result. Simpler than AnswerVerifier: no
    LLM SQL-translation step, since the agent already supplied its own SQL — this only
    re-runs it read-only and diffs. Same "does my number appear anywhere" philosophy as
    AnswerVerifier.cross_check: every numeric cell across every returned row is checked,
    not just the first, so a legitimate GROUP BY claim (row (category, orders, revenue))
    isn't spuriously flagged DIVERGENT for citing a non-first column."""

    def __init__(self, cm):
        self._cm = cm

    def verify(self, claim: dict) -> dict:
        sql = claim.get("sql_query") or ""
        try:
            with self._cm.warehouse(read_only=True) as conn:
                rows = conn.execute(sql).fetchall()
        except Exception as e:
            return {"status": "ERROR", "error": str(e), "rows": [], "sql": sql}

        if not rows:
            return {"status": "EMPTY", "error": None, "rows": [], "sql": sql}

        targets = _extract_target_numbers(claim.get("reported_value"))
        if not targets:
            return {"status": "EMPTY", "error": None, "rows": rows[:25], "sql": sql}

        row_floats = [
            f for row in rows for v in row if (f := _to_float(v)) is not None
        ]
        matched = any(
            any(abs(t - r) <= max(_ABS_TOLERANCE, abs(t) * _REL_TOLERANCE) for r in row_floats)
            for t in targets
        )
        status = "CONSISTENT" if matched else "DIVERGENT"
        return {"status": status, "error": None, "rows": rows[:25], "sql": sql}
