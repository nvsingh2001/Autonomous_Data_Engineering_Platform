from typing import List
from pydantic import BaseModel


class DataEngineeringState(BaseModel):
    data_dir: str = "data"
    reports_dir: str = "reports"
    db_path: str = "data/warehouse.db"
    files: List[str] = []
    profiling_results: str = ""
    quality_report: str = ""
    quality_score: int = 100
    star_schema: str = ""
    clean_sql: str = ""
    kpi_report: str = ""
    final_summary: str = ""
    agent_token_usage: dict[str, dict[str, int]] = {}
    source_row_counts: dict[str, int] = {}
    entity_map: dict[str, str] = {}
    primary_fact_table: str = ""
    verified_metrics: dict = {}
    user_instructions: str = ""
