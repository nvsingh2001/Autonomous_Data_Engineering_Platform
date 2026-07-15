import os
import uuid
from typing import Type

from crewai.tools import BaseTool
from pydantic import BaseModel, PrivateAttr

from schemas import RenderChartInput

_CHART_TYPES = ("bar", "line", "pie")
_ACCENT_COLOR = "#4C72B0"


class RenderChartTool(BaseTool):
    name: str = "render_chart"
    description: str = (
        "Renders a bar, line, or pie chart from labeled numeric data you already retrieved "
        "with run_duckdb_query, and returns a markdown image reference to embed directly in "
        "your answer. Never invent data points — chart only what a query actually returned."
    )
    args_schema: Type[BaseModel] = RenderChartInput
    _charts_dir: str = PrivateAttr()

    def __init__(self, charts_dir: str, **kwargs):
        super().__init__(**kwargs)
        self._charts_dir = charts_dir
        os.makedirs(self._charts_dir, exist_ok=True)

    def _run(
        self, chart_type: str, title: str, labels: list[str], values: list[float]
    ) -> str:
        if chart_type not in _CHART_TYPES:
            return f"Unsupported chart_type '{chart_type}'. Use one of: {_CHART_TYPES}."
        if not labels or len(labels) != len(values):
            return "labels and values must be non-empty and the same length."

        # Imported lazily: this module loads on every chat turn (via SkillRegistry),
        # so a missing matplotlib install must only break chart requests, not chat itself.
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError as e:
            return f"Charting is unavailable: {e}"

        fig, ax = plt.subplots(figsize=(7, 4.5))
        try:
            _draw(ax, chart_type, labels, values)
            ax.set_title(title)
            fig.tight_layout()
            filename = f"{uuid.uuid4().hex}.png"
            fig.savefig(os.path.join(self._charts_dir, filename), dpi=110)
            return f"![{title}](/api/charts/{filename})"
        except Exception as e:
            return f"Error rendering chart: {e}"
        finally:
            plt.close(fig)


def _draw(ax, chart_type: str, labels: list[str], values: list[float]) -> None:
    if chart_type == "bar":
        ax.bar(labels, values, color=_ACCENT_COLOR)
        ax.tick_params(axis="x", rotation=45)
    elif chart_type == "line":
        ax.plot(labels, values, marker="o", color=_ACCENT_COLOR)
        ax.tick_params(axis="x", rotation=45)
    else:
        ax.pie(values, labels=labels, autopct="%1.1f%%")
