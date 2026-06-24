import os
import json


class TokenReporter:
    def __init__(self, usage: dict, reports_dir: str):
        self._usage = usage
        self._reports_dir = reports_dir

    def _totals(self) -> dict:
        return {
            "prompt": sum(m["prompt_tokens"] for m in self._usage.values()),
            "completion": sum(m["completion_tokens"] for m in self._usage.values()),
            "total": sum(m["total_tokens"] for m in self._usage.values()),
            "requests": sum(m["successful_requests"] for m in self._usage.values()),
        }

    def write(self) -> None:
        if not self._usage:
            print("[Flow] No token usage tracked.")
            return
        t = self._totals()
        with open(
            os.path.join(self._reports_dir, "token_usage_report.json"),
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
            os.path.join(self._reports_dir, "token_usage_report.md"),
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
