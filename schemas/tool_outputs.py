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
    status: Literal["PASS", "FAIL"] = Field(..., description="Validation result")
    report: str = Field(..., description="Full markdown validation report")


class KPIOutput(BaseModel):
    report: str = Field(..., description="Full KPI analytics markdown report")


class ReportOutput(BaseModel):
    report: str = Field(..., description="Full executive summary markdown")
