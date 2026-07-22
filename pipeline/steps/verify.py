import json
import os

from utils import AnswerVerifier, ClaimVerifier
from pipeline.core import PipelineStep
from logging_setup import get_logger

_LOG = get_logger("Flow")

_ICON = {"CONSISTENT": "✅", "DIVERGENT": "🚩", "ERROR": "⚠️", "EMPTY": "⚠️"}


class VerifyStep(PipelineStep):
    """Two independent, always-attempted checks, combined into one report: requested
    metrics/questions (AnswerVerifier — an LLM translates the agreed definition to SQL),
    and every self-recorded report claim (ClaimVerifier — re-executes the agent's own
    cited SQL, no translation needed). Both feed the same `state.definitions_diverged`
    corrective-retry gate crew.py already drives."""

    def run(self) -> None:
        def_lines, def_diverged_names, def_diverged, def_unverified = self._verify_definitions()
        claim_lines, claim_diverged_names, claim_diverged, claim_unverified, n_claims = (
            self._verify_claims()
        )

        diverged = def_diverged + claim_diverged
        diverged_names = def_diverged_names + claim_diverged_names
        unverified = def_unverified + claim_unverified

        if diverged_names:
            status = f"REVIEW NEEDED — divergent: {', '.join(diverged_names)}"
        elif unverified:
            status = f"PARTIAL — could not verify: {', '.join(unverified)}"
        else:
            status = "OK"

        lines = ["# Answer Verification", ""] + def_lines + claim_lines + [
            f"Verification Status: {status}"
        ]
        report = "\n".join(lines) + "\n"
        self._write_report("verification_report.md", report)
        _LOG.info(f"Answer verification: {status} ({n_claims} claim(s) checked)")
        self.state.verification_report = report
        # Drive the corrective re-run: which agreed definitions or self-recorded claims
        # the report deviated from, and the note to hand back to the analytics agent.
        self.state.definitions_diverged = diverged
        self.state.analytics_feedback = (
            self._correction_feedback(diverged) if diverged else ""
        )

    def _verify_definitions(self) -> tuple[list[str], list[str], list[dict], list[str]]:
        targets = self._targets(self.state.user_intent)
        if not targets:
            lines = [
                "## Requested Metrics & Questions",
                "",
                "_No confirmed metric definitions or questions were requested for this run._",
                "",
            ]
            return lines, [], [], []

        verifier = AnswerVerifier(self.cm, self._ctx.build_sql_llm())
        kpi_report = self.state.kpi_report
        definitions = self._definitions_text(self.state.user_intent)
        lines = [
            "## Requested Metrics & Questions",
            "",
            "_Each requested metric is independently re-derived from the agreed definition (an "
            "LLM translates the definition into one SQL query; DuckDB executes it), then checked "
            "against the analytics report. A 🚩 DIVERGENT flag means the two independent "
            "derivations disagree — a human should review. It does NOT by itself mean the report "
            "is wrong (the verifier can mis-read a definition too)._",
            "",
        ]
        diverged_names: list[str] = []
        diverged: list[dict] = []
        unverified: list[str] = []
        for name, metric, is_def in targets:
            _LOG.info(f"Verifying metric: {name}")
            r = verifier.recompute(metric, definitions)
            cc = AnswerVerifier.cross_check(r, kpi_report)
            st = cc["status"]
            if st == "DIVERGENT":
                diverged_names.append(name)
                # Only an AGREED definition is ground truth worth enforcing — a divergence
                # there means the analytics report did not compute the metric as defined.
                if is_def:
                    diverged.append(
                        {"kind": "definition", "name": name, "definition": metric, "rows": r["rows"]}
                    )
            elif st in ("EMPTY", "ERROR"):
                unverified.append(name)
            heading = "COULD NOT VERIFY" if st in ("EMPTY", "ERROR") else st
            lines.append(f"### {_ICON.get(st, '•')} {name} — {heading}")
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
        return lines, diverged_names, diverged, unverified

    def _verify_claims(self) -> tuple[list[str], list[str], list[dict], list[str], int]:
        claims = self._read_claims(os.path.join(self.reports_dir, "claims.jsonl"))
        if not claims:
            lines = [
                "## Report Claims (self-recorded by the analytics agent)",
                "",
                "_No claims were recorded this run — template-section figures are UNVERIFIED. "
                "This means the analytics agent recorded nothing (or recording failed) for the "
                "always-on report sections; it does NOT mean those figures are correct._",
                "",
            ]
            return lines, [], [], [], 0

        verifier = ClaimVerifier(self.cm)
        lines = [
            "## Report Claims (self-recorded by the analytics agent)",
            "",
            "_Each claim's own cited SQL is independently re-executed and checked for whether the "
            "claimed number actually appears in the result. A 🚩 DIVERGENT flag means the claim's "
            "cited SQL does not actually support the number reported — a human should review._",
            "",
        ]
        diverged_names: list[str] = []
        diverged: list[dict] = []
        unverified: list[str] = []
        for i, claim in enumerate(claims, 1):
            text = str(claim.get("claim_text") or f"Claim {i}")
            label = text if len(text) <= 80 else text[:77] + "..."
            r = verifier.verify(claim)
            st = r["status"]
            if st == "DIVERGENT":
                diverged_names.append(label)
                diverged.append(
                    {
                        "kind": "claim",
                        "name": label,
                        "definition": claim.get("claim_text", ""),
                        "rows": r["rows"],
                    }
                )
            elif st in ("EMPTY", "ERROR"):
                unverified.append(label)
            heading = "COULD NOT VERIFY" if st in ("EMPTY", "ERROR") else st
            lines.append(f"### {_ICON.get(st, '•')} {label} — {heading}")
            lines.append(f"- Claimed value: {claim.get('reported_value')}")
            if r["error"]:
                lines += [f"- ⚠️ re-execution failed: `{r['error']}`", ""]
            else:
                lines.append("- Independent re-execution:")
                lines += [f"    - {tuple(row)}" for row in r["rows"][:10]]
                lines.append("")
            lines.append(
                f"<details><summary>cited SQL</summary>\n\n```sql\n{claim.get('sql_query', '')}\n```\n</details>"
            )
            lines.append("")
        return lines, diverged_names, diverged, unverified, len(claims)

    def _read_claims(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        claims: list[dict] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    claims.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return claims

    def _correction_feedback(self, diverged: list[dict]) -> str:
        lines = [
            "CORRECTION REQUIRED — a prior draft of this report deviated from the agreed "
            "metric definition(s) or a self-recorded claim's cited SQL below. Recompute ONLY "
            "these, EXACTLY as defined (same buckets/bands/segments, filter, and grain), and "
            "leave every other section unchanged:"
        ]
        for d in diverged:
            if d.get("kind") == "claim":
                lines.append(
                    f"- CLAIM \"{d['name']}\" did not match the number its own cited SQL "
                    "actually returns — re-derive this figure (and the SQL for it) and record "
                    "the corrected claim."
                )
            else:
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
