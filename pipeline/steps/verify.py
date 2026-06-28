"""Answer-verification step (runs after analytics).

For each user-confirmed metric definition (or, failing that, each stated question), the
metric is independently re-derived from the agreed definition — an LLM translates the
definition into ONE SQL query, DuckDB executes it deterministically — and the result is
cross-checked against the analytics report. Divergences are flagged in
`verification_report.md` for human review.

This is a *divergence detector*, not an oracle: the verifier's own translation can also
mis-read a definition, so a flag means "these two independent derivations disagree — look
here", not "the analytics report is wrong". It exists to turn silent answer errors (e.g.
computing gross while labelling it 'net') into loud flags.
"""

import os

from pipeline.answer_verifier import recompute, cross_check_report

_ICON = {"CONSISTENT": "✅", "DIVERGENT": "🚩", "ERROR": "⚠️", "EMPTY": "⚠️"}


class VerifyStep:
    def __init__(self, db_path: str, reports_dir: str):
        self._db_path = db_path
        self._reports_dir = reports_dir

    def run(self, user_intent: dict, kpi_report: str) -> str:
        path = os.path.join(self._reports_dir, "verification_report.md")
        targets = self._targets(user_intent)
        if not targets:
            report = (
                "# Answer Verification\n\n"
                "No confirmed metric definitions or questions to verify for this run.\n\n"
                "Verification Status: N/A\n"
            )
            self._write(path, report)
            return report

        llm = self._build_sql_llm()
        definitions = self._definitions_text(user_intent)
        lines = [
            "# Answer Verification (independent recompute)",
            "",
            "_Each requested metric is independently re-derived from the agreed definition (an "
            "LLM translates the definition into one SQL query; DuckDB executes it), then checked "
            "against the analytics report. A 🚩 DIVERGENT flag means the two independent "
            "derivations disagree — a human should review. It does NOT by itself mean the report "
            "is wrong (the verifier can mis-read a definition too)._",
            "",
        ]
        diverged: list[str] = []
        unverified: list[str] = []  # verifier's own recompute failed (NULL/empty or SQL error)
        for name, metric in targets:
            print(f"[Flow] Verifying metric: {name}")
            r = recompute(self._db_path, metric, definitions, llm)
            cc = cross_check_report(r, kpi_report)
            st = cc["status"]
            if st == "DIVERGENT":
                diverged.append(name)
            elif st in ("EMPTY", "ERROR"):
                unverified.append(name)
            heading = "COULD NOT VERIFY" if st in ("EMPTY", "ERROR") else st
            lines.append(f"## {_ICON.get(st, '•')} {name} — {heading}")
            if r["error"]:
                lines += [
                    f"- ⚠️ verifier's own query failed (metric NOT independently checked): "
                    f"`{r['error']}`",
                    "",
                ]
            else:
                lines.append("- Independent recomputation:")
                lines += [f"    - {tuple(row)}" for row in r["rows"][:25]]
                if st == "EMPTY":
                    lines.append(
                        "- ⚠️ Recompute produced no comparable number (e.g. NULL/empty result) — "
                        "this metric was NOT independently verified."
                    )
                if cc["missing"]:
                    lines.append("- 🚩 Recomputed figure(s) NOT found in the analytics report:")
                    lines += [
                        f"    - {m['label'] or 'value'}: {m['value']:,.2f}"
                        for m in cc["missing"]
                    ]
            lines.append(
                f"<details><summary>recompute SQL</summary>\n\n```sql\n{r['sql']}\n```\n</details>"
            )
            lines.append("")
        # DIVERGENT is a hard flag (verified disagreement); EMPTY/ERROR means the verifier itself
        # could not produce a check — surfaced as PARTIAL, never silently folded into OK.
        if diverged:
            status = f"REVIEW NEEDED — divergent: {', '.join(diverged)}"
        elif unverified:
            status = f"PARTIAL — could not verify: {', '.join(unverified)}"
        else:
            status = "OK"
        lines.append(f"Verification Status: {status}")
        report = "\n".join(lines) + "\n"
        self._write(path, report)
        print(f"[Flow] Answer verification: {status}")
        return report

    def _targets(self, user_intent: dict) -> list[tuple[str, str]]:
        """What to recompute. The user's QUESTIONS are the real asks (the agreed metric
        definitions are passed separately as ground-truth context), so verify those. Fall back
        to the raw definitions only when no questions were captured."""
        intent = user_intent or {}
        questions = [
            (f"Q{i}", str(q))
            for i, q in enumerate(intent.get("questions") or [], 1)
            if str(q).strip()
        ]
        if questions:
            return questions
        defs = intent.get("metric_definitions") or []
        return [
            (str(d.get("name") or "metric"), str(d.get("definition") or ""))
            for d in defs
            if isinstance(d, dict) and str(d.get("definition") or "").strip()
        ]

    def _definitions_text(self, user_intent: dict) -> str:
        defs = (user_intent or {}).get("metric_definitions") or []
        rendered = "\n".join(
            f"- {d.get('name')}: {d.get('definition')}"
            for d in defs
            if isinstance(d, dict) and d.get("definition")
        )
        return rendered or "(none)"

    def _build_sql_llm(self):
        """Mirror AgentFactory's provider auto-selection for the SQL model."""
        model = os.environ.get("SQL_MODEL") or os.environ.get(
            "PIPELINE_MODEL", "ollama/gemma4:31b-cloud"
        )
        if model.startswith("bedrock/"):
            from agents.providers import BedrockProvider

            return BedrockProvider(model, os.environ.get("SQL_AWS_REGION") or None).create(0.0)
        if model.startswith("ollama/"):
            from agents.providers import OllamaProvider

            return OllamaProvider(
                model, os.environ.get("PIPELINE_BASE_URL", "http://localhost:11434")
            ).create(0.0)
        from agents.providers import CloudProvider

        return CloudProvider(model).create(0.0)

    def _write(self, path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
