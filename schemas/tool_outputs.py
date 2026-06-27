from typing import Literal
from pydantic import BaseModel, Field


class QualityOutput(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Quality score from 0 to 100")
    report: str = Field(..., description="Full markdown quality report")


class SchemaOutput(BaseModel):
    report: str = Field(..., description="Full star schema markdown document")


class SchemaPlanOutput(BaseModel):
    tables: list[dict] = Field(
        ...,
        description="List of warehouse table specs: [{name, type, sources, description}, ...]",
    )


class SQLOutput(BaseModel):
    sql: str = Field(..., description="DuckDB SQL code for one warehouse table")


class ValidationOutput(BaseModel):
    # Field ORDER matters: `report` is declared first so the model writes the full
    # per-check analysis before emitting the verdict, making `status` a conclusion
    # conditioned on the report. Declaring `status` first made the model commit to a
    # verdict up front (e.g. "FAIL", primed by the prompt's many "FAIL if..." rules)
    # and then write a report that concluded PASS — the two disagreed.
    report: str = Field(
        ...,
        description="Full markdown validation report; ends with 'Validation Status: PASS' or 'FAIL'",
    )
    status: Literal["PASS", "FAIL"] = Field(
        ...,
        description="Final verdict — MUST match the 'Validation Status:' line at the end of report",
    )


class KPIOutput(BaseModel):
    report: str = Field(..., description="Full KPI analytics markdown report")


class ReportOutput(BaseModel):
    report: str = Field(..., description="Full executive summary markdown")
