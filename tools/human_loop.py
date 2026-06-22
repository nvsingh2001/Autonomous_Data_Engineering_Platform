import sys
from abc import ABC, abstractmethod
from typing import Callable


class ApprovalStrategy(ABC):
    @abstractmethod
    def request(self, score: float, summary: str) -> bool: ...


class CLIApprovalStrategy(ApprovalStrategy):
    def request(self, score: float, summary: str) -> bool:
        print("\n" + "=" * 60)
        print("WARNING: DATA QUALITY ALERT")
        print(f"Dataset Quality Score: {score}/100 (Threshold is 80)")
        print(f"Key issues identified:\n{summary}")
        print("=" * 60)
        sys.stdout.write("\nDo you approve proceeding? (yes/no): ")
        sys.stdout.flush()
        line = sys.stdin.readline().strip().lower()
        return line in ("yes", "y", "approve")


class WebApprovalStrategy(ApprovalStrategy):
    def __init__(self, callback: Callable[[float, str], bool]):
        self._callback = callback

    def request(self, score: float, summary: str) -> bool:
        return self._callback(score, summary)


class HumanLoopService:
    _strategy: ApprovalStrategy = CLIApprovalStrategy()

    @classmethod
    def set_strategy(cls, strategy: ApprovalStrategy) -> None:
        cls._strategy = strategy

    @classmethod
    def request_human_approval(cls, score: float, issues_summary: str) -> bool:
        return cls._strategy.request(score, issues_summary)
