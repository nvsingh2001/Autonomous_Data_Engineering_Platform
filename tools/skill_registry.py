import os
import re

from .chart_tool import RenderChartTool
from .export_tool import ExportCSVTool
from .schema_lookup_tool import LookupSchemaTool

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)

_TOOL_BUILDERS = {
    "chart": lambda ctx: [RenderChartTool(charts_dir=ctx["charts_dir"])],
    "memory_search": lambda ctx: [
        t
        for t in ctx["registry"].get_memory_tools()
        if t.name == "search_past_executions"
    ],
    "export_csv": lambda ctx: [
        ExportCSVTool(
            data_dir=ctx["data_dir"],
            exports_dir=ctx["exports_dir"],
            connection_manager=ctx["connection_manager"],
        )
    ],
    "schema_lookup": lambda ctx: [LookupSchemaTool(schema_path=ctx["schema_path"])],
}


class Skill:
    def __init__(self, name: str, description: str, body: str):
        self.name = name
        self.description = description
        self.body = body


def _parse_skill_md(path: str) -> Skill:
    text = open(path, encoding="utf-8").read()
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"Malformed SKILL.md (missing frontmatter): {path}")
    front, body = match.groups()
    meta = dict(_FIELD_RE.findall(front))
    return Skill(name=meta["name"], description=meta["description"], body=body.strip())


def _load_skills(skills_dir: str) -> dict[str, Skill]:
    if not os.path.isdir(skills_dir):
        return {}
    skills = {}
    for entry in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, entry, "SKILL.md")
        if os.path.isfile(skill_md):
            skill = _parse_skill_md(skill_md)
            skills[skill.name] = skill
    return skills


class SkillRegistry:
    """Progressive-disclosure catalog of optional chat-analyst capabilities."""

    def __init__(self, skills_dir: str = "skills", **ctx):
        self._skills = _load_skills(skills_dir)
        self._ctx = ctx

    def catalog(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self._skills.values())

    def load(self, names: list[str]) -> tuple[str, list]:
        picked = [self._skills[n] for n in names if n in self._skills]
        instructions = "\n\n".join(s.body for s in picked)
        tools = [
            tool
            for s in picked
            for tool in _TOOL_BUILDERS.get(s.name, lambda _ctx: [])(self._ctx)
        ]
        return instructions, tools
