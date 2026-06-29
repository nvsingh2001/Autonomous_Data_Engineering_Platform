import os
import json
from crewai import Crew


class TokenReporter:
    def __init__(self) -> None:
        self._usage: dict[str, dict[str, int]] = {}

    def track(self, crew: Crew) -> None:
        for agent in crew.agents:
            role = agent.role
            if not hasattr(agent, "llm") or not hasattr(
                agent.llm, "get_token_usage_summary"
            ):
                continue
            try:
                usage = agent.llm.get_token_usage_summary()
                bucket = self._usage.setdefault(
                    role,
                    {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "successful_requests": 0,
                    },
                )
                bucket["prompt_tokens"] += usage.prompt_tokens
                bucket["completion_tokens"] += usage.completion_tokens
                bucket["total_tokens"] += usage.total_tokens
                bucket["successful_requests"] += usage.successful_requests
            except Exception as e:
                print(f"[Flow] Warning: token tracking failed for '{role}': {e}")

    def _totals(self) -> dict:
        return {
            "prompt": sum(m["prompt_tokens"] for m in self._usage.values()),
            "completion": sum(m["completion_tokens"] for m in self._usage.values()),
            "total": sum(m["total_tokens"] for m in self._usage.values()),
            "requests": sum(m["successful_requests"] for m in self._usage.values()),
        }

    def write(self, reports_dir: str) -> None:
        if not self._usage:
            print("[Flow] No token usage tracked.")
            return
        t = self._totals()
        with open(
            os.path.join(reports_dir, "token_usage_report.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self._usage, f, indent=2)

        rows = [
            "| Agent / Role | Prompt Tokens | Completion Tokens | Total Tokens | Requests |",
            "|---|---|---|---|---|",
        ]
        for role, m in self._usage.items():
            rows.append(
                f"| {role} | {m['prompt_tokens']:,} | {m['completion_tokens']:,} "
                f"| {m['total_tokens']:,} | {m['successful_requests']} |"
            )
        rows.append(
            f"| **TOTAL** | **{t['prompt']:,}** | **{t['completion']:,}** "
            f"| **{t['total']:,}** | **{t['requests']}** |"
        )
        md = "# Pipeline Token Usage Report\n\n## Summary Table\n\n" + "\n".join(rows)
        with open(
            os.path.join(reports_dir, "token_usage_report.md"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write(md)

        print("\n" + "=" * 55)
        print("           TOKEN USAGE SUMMARY")
        print("=" * 55)
        for role, m in self._usage.items():
            print(f"Agent: {role}")
            print(f"  Prompt:     {m['prompt_tokens']:,}")
            print(f"  Completion: {m['completion_tokens']:,}")
            print(f"  Total:      {m['total_tokens']:,}")
            print(f"  Requests:   {m['successful_requests']}")
        print("-" * 55)
        print(f"TOTAL PROMPT:     {t['prompt']:,}")
        print(f"TOTAL COMPLETION: {t['completion']:,}")
        print(f"TOTAL TOKENS:     {t['total']:,}")
        print(f"TOTAL REQUESTS:   {t['requests']}")
        print("=" * 55 + "\n")
