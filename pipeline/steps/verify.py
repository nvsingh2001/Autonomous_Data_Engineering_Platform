from utils import AnswerVerifier
from pipeline.core import PipelineStep
from logging_setup import get_logger

_LOG = get_logger("Flow")

_ICON = {"CONSISTENT": "✅", "DIVERGENT": "🚩", "ERROR": "⚠️", "EMPTY": "⚠️"}


class VerifyStep(PipelineStep):
    def run(self) -> None:
        targets = self._targets(self.state.user_intent)
        if not targets:
            report = (
                "# Answer Verification\n\n"
                "No confirmed metric definitions or questions to verify for this run.\n\n"
                "Verification Status: N/A\n"
            )
            self._write_report("verification_report.md", report)
            self.state.verification_report = report
            return

        verifier = AnswerVerifier(self.cm, self._ctx.build_sql_llm())
        kpi_report = self.state.kpi_report
        definitions = self._definitions_text(self.state.user_intent)
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
        unverified: list[
            str
        ] = []  # verifier's own recompute failed (NULL/empty or SQL error)
        diverged_defs: list[dict] = []  # agreed definitions the report deviated from
        for name, metric, is_def in targets:
            _LOG.info(f"Verifying metric: {name}")
            r = verifier.recompute(metric, definitions)
            cc = AnswerVerifier.cross_check(r, kpi_report)
            st = cc["status"]
            if st == "DIVERGENT":
                diverged.append(name)
                # Only an AGREED definition is ground truth worth enforcing — a divergence
                # there means the analytics report did not compute the metric as defined.
                if is_def:
                    diverged_defs.append(
                        {"name": name, "definition": metric, "rows": r["rows"]}
                    )
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
                    lines.append(
                        "- 🚩 Recomputed figure(s) NOT found in the analytics report:"
                    )
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
        self._write_report("verification_report.md", report)
        _LOG.info(f"Answer verification: {status}")
        self.state.verification_report = report
        # Drive the corrective re-run: which agreed definitions the report deviated from,
        # and the note to hand back to the analytics agent.
        self.state.definitions_diverged = diverged_defs
        self.state.analytics_feedback = (
            self._correction_feedback(diverged_defs) if diverged_defs else ""
        )

    def _correction_feedback(self, diverged: list[dict]) -> str:
        lines = [
            "CORRECTION REQUIRED — a prior draft of this report deviated from the agreed "
            "metric definition(s) below. Recompute ONLY these, EXACTLY as defined (same "
            "buckets/bands/segments, filter, and grain), and leave every other section "
            "unchanged:"
        ]
        for d in diverged:
            lines.append(f"- {d['name']}: {d['definition']}")
            rows = d.get("rows") or []
            if rows:
                preview = "; ".join(str(tuple(r)) for r in rows[:8])
                lines.append(
                    f"    (an independent recompute of this exact definition yielded: {preview})"
                )
        return "\n".join(lines)

    def _targets(self, user_intent: dict) -> list[tuple[str, str, bool]]:
        """What to recompute, as (name, metric, is_agreed_definition). We anchor on the
        AGREED DEFINITIONS when present — they are the ground truth to enforce, so recomputing
        them (not the looser question text) is what catches an analytics deviation. Fall back
        to the raw questions only when no definitions were captured (flag-only, nothing to
        enforce)."""
        intent = user_intent or {}
        defs = intent.get("metric_definitions") or []
        def_targets = [
            (str(d.get("name") or "metric"), str(d.get("definition") or ""), True)
            for d in defs
            if isinstance(d, dict) and str(d.get("definition") or "").strip()
        ]
        if def_targets:
            return def_targets
        return [
            (f"Q{i}", str(q), False)
            for i, q in enumerate(intent.get("questions") or [], 1)
            if str(q).strip()
        ]

    def _definitions_text(self, user_intent: dict) -> str:
        defs = (user_intent or {}).get("metric_definitions") or []
        rendered = "\n".join(
            f"- {d.get('name')}: {d.get('definition')}"
            for d in defs
            if isinstance(d, dict) and d.get("definition")
        )
        return rendered or "(none)"
