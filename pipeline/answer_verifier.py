"""Thin answer-verification layer.

For each user-confirmed metric definition, independently translate the definition into
ONE DuckDB query (a narrow, focused task — not the multi-section analytics sprawl),
execute it deterministically, and compare the result to the figure the analytics agent
reported. Divergence beyond tolerance is flagged for human review.

The division of labour is the same one that worked for structural validation:
the LLM only TRANSLATES an already-precise, human-confirmed definition into SQL (its
strength); CODE executes the query and compares the numbers (reliable). Because the
translation is independent of the analytics agent's own SQL, a silent mis-implementation
(e.g. computing gross while labelling it "net") shows up as a divergence.
"""

import re
import duckdb


def schema_text(conn: duckdb.DuckDBPyConnection) -> str:
    """One line per table: name(col type, ...). Grounds the translation in real columns."""
    lines = []
    for (t,) in conn.execute("SHOW TABLES").fetchall():
        cols = conn.execute(f"DESCRIBE {t}").fetchall()
        lines.append(f"{t}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
    return "\n".join(lines)


_SYS = (
    "You translate ONE confirmed business-metric definition into a single DuckDB SQL query. "
    "Output only the SQL — no prose, no markdown fences."
)

_PROMPT = (
    "Write a SINGLE DuckDB SQL query that computes EXACTLY the metric below. Every clause of the "
    "definition — numerator, denominator, filter, time grain, and any population/attribution rule "
    "— must be reflected literally in the SQL. Do not add, drop, or reinterpret any part; if it "
    "says 'minus refunds', subtract refunds. If the definition names a specific column or "
    "identifier — e.g. an entity key such as `customer_unique_id` — use THAT exact column, never "
    "a similar-looking one (such as a per-row or per-order id); using the wrong key silently "
    "changes the answer. If the metric splits into groups (e.g. new vs returning), return one row "
    "per group with a clear text label column and the numeric value. Output ONLY the SQL.\n\n"
    "WAREHOUSE SCHEMA:\n{schema}\n\n"
    "CONFIRMED DEFINITIONS (ground truth):\n{definitions}\n\n"
    "METRIC TO COMPUTE:\n{metric}"
)


def _strip_sql(raw: str) -> str:
    s = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
    # keep from the first SELECT/WITH onward
    m = re.search(r"\b(WITH|SELECT)\b", s, re.IGNORECASE)
    return s[m.start():].strip() if m else s


def _ask_sql(llm, messages) -> str:
    raw = llm.call(messages)
    return _strip_sql(raw if isinstance(raw, str) else str(raw))


def recompute(db_path: str, metric: str, definitions: str, llm, max_fix: int = 1) -> dict:
    """Translate `metric` (using `definitions` as ground truth) to SQL, run it, return rows.
    On a SQL error, feed the error back to the model up to `max_fix` times for a correction."""
    conn = duckdb.connect(db_path, read_only=True)
    try:
        prompt = _PROMPT.format(
            schema=schema_text(conn), definitions=definitions or "(none)", metric=metric
        )
        messages = [
            {"role": "system", "content": _SYS},
            {"role": "user", "content": prompt},
        ]
        sql = _ask_sql(llm, messages)
        last_err = None
        for _ in range(max_fix + 1):
            try:
                cur = conn.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                return {"metric": metric, "sql": sql, "columns": cols, "rows": rows, "error": None}
            except Exception as e:
                last_err = str(e)
                messages += [
                    {"role": "assistant", "content": sql},
                    {
                        "role": "user",
                        "content": f"That query failed with:\n{last_err}\n"
                        "Return a corrected single DuckDB query. Output ONLY the SQL.",
                    },
                ]
                sql = _ask_sql(llm, messages)
        return {"metric": metric, "sql": sql, "columns": [], "rows": [], "error": last_err}
    finally:
        conn.close()


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _format_variants(n: float) -> list[str]:
    """String forms a number might plausibly appear as in a markdown report."""
    out = set()
    for val in (n, round(n, 2), float(round(n))):
        out.add(f"{val:,.2f}")  # 1,816,472.70
        out.add(f"{val:,.0f}")  # 1,816,473
        out.add(f"{val:.2f}")   # 1816472.70
        out.add(f"{val:.0f}")   # 1816473
    return [s for s in out if s]


def cross_check_report(recomputed: dict, report_text: str) -> dict:
    """Robust divergence heuristic: for each numeric value in the recomputed rows, check
    whether a matching number (to the dollar) appears in the analytics report. Avoids brittle
    per-metric parsing — we only ask "does my independently-computed figure show up at all?".
    Returns {found, missing, status}. status DIVERGENT => the report lacks a recomputed figure."""
    report = report_text or ""
    found: list[dict] = []
    missing: list[dict] = []
    for row in recomputed.get("rows", []):
        label = next((str(v) for v in row if _num(v) is None), "")
        for v in row:
            n = _num(v)
            if n is None or abs(n) < 0.005:  # skip non-numbers and trivial zeros
                continue
            entry = {"label": label, "value": n}
            (found if any(s in report for s in _format_variants(n)) else missing).append(entry)
    if recomputed.get("error"):
        status = "ERROR"
    elif not (found or missing):
        status = "EMPTY"
    else:
        status = "DIVERGENT" if missing else "CONSISTENT"
    return {"found": found, "missing": missing, "status": status}


def compare(recomputed: dict, agent_values: dict[str, float], tol: float = 0.01) -> list[dict]:
    """Match each agent-claimed {label: value} to the nearest numeric in the recomputed rows;
    flag when the relative difference exceeds `tol`. Label matching is case-insensitive substring."""
    # flatten recomputed rows into (label, value) pairs
    pairs: list[tuple[str, float]] = []
    for row in recomputed.get("rows", []):
        label = next((str(v) for v in row if _num(v) is None), "")
        for v in row:
            n = _num(v)
            if n is not None:
                pairs.append((label, n))
    findings = []
    for label, claimed in agent_values.items():
        cand = [(lab, val) for lab, val in pairs if label.lower() in lab.lower()] or pairs
        best = min(cand, key=lambda p: abs(p[1] - claimed), default=(None, None))
        recomp = best[1]
        if recomp is None:
            findings.append({"label": label, "claimed": claimed, "recomputed": None,
                             "status": "NO_MATCH"})
            continue
        denom = max(abs(claimed), abs(recomp), 1.0)
        rel = abs(claimed - recomp) / denom
        findings.append({
            "label": label, "claimed": claimed, "recomputed": recomp,
            "rel_diff_pct": round(rel * 100, 2),
            "status": "MATCH" if rel <= tol else "MISMATCH",
        })
    return findings
