from pydantic import BaseModel, Field


class SQLQueryInput(BaseModel):
    query: str = Field(..., description="SQL query to execute.")


class ProfileCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to file.")


class PreviewCSVInput(BaseModel):
    file_path: str = Field(..., description="Path to dataset file.")
    limit: int = Field(5, description="Number of rows to preview.")


class SaveExecutionInput(BaseModel):
    category: str = Field(
        ...,
        description=(
            "Category — one of: 'error_patterns', 'schema_decisions', 'data_quality', 'analytics_insights'. "
            "Do NOT store raw SQL or dataset-specific column names. "
            "Store only the error TYPE and fix STRATEGY (e.g. 'UNION branch column count mismatch — "
            "add NULL placeholders to shorter branch'), entity-level schema decisions "
            "(e.g. 'payments entity → Fact_Payments, grain = one row per installment'), "
            "or reusable analytics patterns "
            "(e.g. 'MoM growth window function pattern for date-grained fact tables', "
            "'payment_type breakdown safe when Fact_Payments has >1k rows')."
        ),
    )
    key: str = Field(
        ..., description="Descriptive identifier, e.g. 'union_column_count_fix'."
    )
    content: str = Field(
        ...,
        description=(
            "Content to save. Must be dataset-agnostic: describe patterns, strategies, and entity-level "
            "decisions — NOT specific column names, table names, or SQL from the current dataset."
        ),
    )


class SearchExecutionsInput(BaseModel):
    category: str = Field(
        ...,
        description="Category to search: 'error_patterns', 'schema_decisions', or 'data_quality'.",
    )
    query: str = Field(..., description="Search term or issue details.")
    limit: int = Field(3, description="Max number of records to return (max 5).")
