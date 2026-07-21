from pydantic import BaseModel, Field


class SchemaOutput(BaseModel):
    report: str = Field(..., description="Full star schema markdown document")


class SchemaPlanOutput(BaseModel):
    tables: list[dict] = Field(
        ...,
        description="List of warehouse table specs: [{name, type, sources, description}, ...]",
    )


class SQLOutput(BaseModel):
    sql: str = Field(..., description="DuckDB SQL code for one warehouse table")
    primary_key: str = Field(
        ...,
        description=(
            "Exact column name (as selected in the SQL) that uniquely identifies one row "
            "of this table once built — the GROUP BY / DISTINCT ON key for a dimension. "
            "Empty string if the table has no single natural row key (e.g. some fact tables)."
        ),
    )


class ReportOutput(BaseModel):
    report: str = Field(..., description="Full executive summary markdown")
