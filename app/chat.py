import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crewai import LLM, Crew, Task
from config import (
    PIPELINE_MODEL,
    PIPELINE_BASE_URL,
    PIPELINE_API_KEY,
    BI_MODEL,
    BI_AWS_REGION,
)

CHARTS_DIR = os.path.join("reports", "charts")
EXPORTS_DIR = os.path.join("reports", "exports")
SCHEMA_PATH = os.path.join("reports", "schema_design.md")


def _route_skills(question: str, catalog: str) -> list[str]:
    """Cheap classification call: which optional skills (if any) does this question
    need? Keeps skill instructions/tool schemas out of the main task prompt unless
    they're actually relevant — the progressive-disclosure step."""
    if not catalog:
        return []
    kwargs: dict = {
        "model": PIPELINE_MODEL,
        "base_url": PIPELINE_BASE_URL,
        "temperature": 0.0,
    }
    if PIPELINE_API_KEY:
        kwargs["api_key"] = PIPELINE_API_KEY
    msgs = [
        {
            "role": "system",
            "content": (
                "Pick which of these optional skills (if any) are needed to answer the "
                "user's question. Reply with a comma-separated list of skill names only, "
                "or 'none'.\n\n" + catalog
            ),
        },
        {"role": "user", "content": question},
    ]
    try:
        raw = LLM(**kwargs).call(msgs).strip().lower()
    except Exception:
        return []
    if raw in ("", "none"):
        return []
    return [name.strip() for name in raw.split(",") if name.strip()]


def run_chat_query(question: str, db_path: str, entity_map: dict) -> str:
    from agents.factory import AgentFactory
    from tools import ToolRegistry, ConnectionManager
    from tools.skill_registry import SkillRegistry

    entity_text = "\n".join(f"  - {fn}: {entity}" for fn, entity in entity_map.items())

    cm = ConnectionManager(db_path, "data")
    registry = ToolRegistry(
        data_dir="data",
        chroma_db_path=".chroma",
        db_path=db_path,
        entity_map=entity_map,
        connection_manager=cm,
    )
    factory = AgentFactory(
        model_name=PIPELINE_MODEL,
        base_url=PIPELINE_BASE_URL,
        tool_registry=registry,
        bi_model_name=BI_MODEL,
        bi_region=BI_AWS_REGION,
    )

    skills = SkillRegistry(
        charts_dir=CHARTS_DIR,
        exports_dir=EXPORTS_DIR,
        schema_path=SCHEMA_PATH,
        data_dir="data",
        connection_manager=cm,
        registry=registry,
    )
    skill_names = _route_skills(question, skills.catalog())
    extra_instructions, extra_tools = skills.load(skill_names)

    analyst = factory.create_chat_analyst(extra_tools=extra_tools)

    task = Task(
        description=(
            "Answer the following question about the warehouse database.\n\n"
            f"ENTITY MAP:\n{entity_text}\n\n"
            f"USER QUESTION:\n{question}\n\n"
            "Instructions:\n"
            "- Run SHOW TABLES first to confirm available tables\n"
            "- Write and execute targeted SQL using run_duckdb_query\n"
            "- Be concise: 2-4 sentences or a short bulleted list with actual numbers\n"
            "- If the question cannot be answered from the available data, say so clearly\n"
            "- Do NOT invent or estimate values — only report what queries return"
            + (
                f"\n\nADDITIONAL CAPABILITY AVAILABLE THIS TURN:\n{extra_instructions}"
                if extra_instructions
                else ""
            )
        ),
        expected_output=(
            "A concise factual answer with specific numbers from query results. "
            "2-4 sentences or a short bulleted list."
        ),
        agent=analyst,
    )

    crew = Crew(agents=[analyst], tasks=[task], verbose=False)
    result = crew.kickoff(inputs={})
    return result.raw
