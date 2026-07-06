import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crewai import Crew, Task
from config import PIPELINE_MODEL, PIPELINE_BASE_URL, BI_MODEL, BI_AWS_REGION


def run_chat_query(question: str, db_path: str, entity_map: dict) -> str:
    from agents.factory import AgentFactory
    from tools import ToolRegistry, ConnectionManager

    schema_context = ""
    schema_path = os.path.join("reports", "schema_design.md")
    if os.path.exists(schema_path):
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_context = f.read()[:3000]

    entity_text = "\n".join(f"  - {fn}: {entity}" for fn, entity in entity_map.items())

    registry = ToolRegistry(
        data_dir="data",
        chroma_db_path=".chroma",
        db_path=db_path,
        entity_map=entity_map,
        connection_manager=ConnectionManager(db_path, "data"),
    )
    factory = AgentFactory(
        model_name=PIPELINE_MODEL,
        base_url=PIPELINE_BASE_URL,
        tool_registry=registry,
        bi_model_name=BI_MODEL,
        bi_region=BI_AWS_REGION,
    )
    analyst = factory.create_chat_analyst()

    task = Task(
        description=(
            "Answer the following question about the warehouse database.\n\n"
            f"SCHEMA DESIGN:\n{schema_context}\n\n"
            f"ENTITY MAP:\n{entity_text}\n\n"
            f"USER QUESTION:\n{question}\n\n"
            "Instructions:\n"
            "- Run SHOW TABLES first to confirm available tables\n"
            "- Write and execute targeted SQL using run_duckdb_query\n"
            "- Be concise: 2-4 sentences or a short bulleted list with actual numbers\n"
            "- If the question cannot be answered from the available data, say so clearly\n"
            "- Do NOT invent or estimate values — only report what queries return"
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
