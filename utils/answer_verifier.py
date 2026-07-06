"""Independent answer-verification service.

For each user-confirmed metric definition, independently translate the definition into
ONE DuckDB query (a narrow, focused task — not the multi-section analytics sprawl),
execute it deterministically, and compare the result to the figure the analytics agent
reported. Divergence is flagged for human review.

The division of labour is the same one that worked for structural validation:
the LLM only TRANSLATES an already-precise, human-confirmed definition into SQL (its
strength); CODE executes the query and compares the numbers (reliable). Because the
translation is independent of the analytics agent's own SQL, a silent mis-implementation
(e.g. computing gross while labelling it "net") shows up as a divergence.
"""

import os
import re

import duckdb
import yaml

_VERIFY_CONFIG_PATH = os.path.join("config", "verify.yaml")
with open(_VERIFY_CONFIG_PATH, "r", encoding="utf-8") as f:
    _VERIFY_CFG = yaml.safe_load(f)["verify_metric"]

_SYS = _VERIFY_CFG["system_prompt"]
_PROMPT = _VERIFY_CFG["user_prompt"]



def _schema_text(conn: duckdb.DuckDBPyConnection) -> str:
    """One line per table: name(col type, ...). Grounds the translation in real columns."""
    lines = []
    for (t,) in conn.execute("SHOW TABLES").fetchall():
        cols = conn.execute(f"DESCRIBE {t}").fetchall()
        lines.append(f"{t}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
    return "\n".join(lines)


def _strip_sql(raw: str) -> str:
    s = re.sub(r"```[a-zA-Z]*", "", raw).replace("```", "").strip()
    # keep from the first SELECT/WITH onward
    m = re.search(r"\b(WITH|SELECT)\b", s, re.IGNORECASE)
    return s[m.start():].strip() if m else s


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


class AnswerVerifier:
    """Re-derives a requested metric from its confirmed definition, independently of the
    analytics agent: the LLM translates the definition into one DuckDB query, the run's
    ConnectionManager executes it read-only, and the figure is cross-checked against the
    analytics report. Bound to a per-run connection manager and SQL LLM."""

    def __init__(self, cm, llm):
        self._cm = cm
        self._llm = llm

    def _ask_sql(self, messages) -> str:
        raw = self._llm.call(messages)
        return _strip_sql(raw if isinstance(raw, str) else str(raw))

    def recompute(self, metric: str, definitions: str, max_fix: int = 1) -> dict:
        """Translate `metric` (using `definitions` as ground truth) to SQL, run it, return rows.
        On a SQL error, feed the error back to the model up to `max_fix` times for a correction.
        Uses a read-only warehouse connection — this is an independent check, never a writer."""
        with self._cm.warehouse(read_only=True) as conn:
            prompt = _PROMPT.format(
                schema=_schema_text(conn), definitions=definitions or "(none)", metric=metric
            )
            messages = [
                {"role": "system", "content": _SYS},
                {"role": "user", "content": prompt},
            ]
            sql = self._ask_sql(messages)
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
                    sql = self._ask_sql(messages)
            return {"metric": metric, "sql": sql, "columns": [], "rows": [], "error": last_err}

    @staticmethod
    def cross_check(recomputed: dict, report_text: str) -> dict:
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
