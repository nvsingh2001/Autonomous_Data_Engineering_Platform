import os
import re

from .chart_tool import RenderChartTool

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w+):\s*(.+)$", re.MULTILINE)

# Maps a skill's name (from its SKILL.md frontmatter) to a builder of the CrewAI
# tool instances it unlocks. Kept as one data-driven table rather than growing an
# if/elif chain as more skills are added.
_TOOL_BUILDERS = {
    "chart": lambda ctx: [RenderChartTool(charts_dir=ctx["charts_dir"])],
    # Reuses the already-constructed ToolRegistry rather than re-deriving
    # dataset_tag/entity_types here. Only exposes search, not save: writing
    # ad hoc chat-derived "memories" into the pipeline's curated pattern store
    # has no guardrail against polluting it with irrelevant content.
    "memory_search": lambda ctx: [
        t
        for t in ctx["registry"].get_memory_tools()
        if t.name == "search_past_executions"
    ],
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
    """Progressive-disclosure catalog of optional chat-analyst capabilities.

    Only name+description (catalog()) is meant for every turn's routing prompt;
    a skill's full instructions and tools are pulled in via load() only once a
    turn's router has picked it, so unrelated capabilities never bloat the
    agent's context or tool schema.
    """

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
