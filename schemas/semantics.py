from typing import Literal
from pydantic import BaseModel, Field

StructuralRole = Literal[
    "monetary_amount",
    "unique_identifier",
    "temporal",
    "quantity",
    "categorical_label",
    "geographic",
    "free_text",
]

BusinessRole = Literal[
    "payment_type",
    "order_status",
    "order_identifier",
    "customer_identifier",
    "product_identifier",
    "seller_identifier",
    "review_score",
    "shipping_method",
    "estimated_delivery_date",
    "actual_delivery_date",
    "category_label",
    "category_translation_reference",
    "none",
]


class ColumnRoleProposal(BaseModel):
    file: str = Field(..., description="Source file name the column belongs to")
    column: str = Field(..., description="Exact column name as it appears in the file")
    structural_role: StructuralRole = Field(
        ..., description="Dataset-agnostic structural role, judged from sample values"
    )
    business_role: BusinessRole = Field(
        ..., description="E-commerce business concept, or 'none' if it doesn't fit one"
    )
    evidence: str = Field(
        ...,
        description="Short phrase citing the actual sample values that justify the roles",
    )
    expected_cardinality: Literal["unique", "repeating"] = Field(
        ...,
        description=(
            "Whether this column is expected to be unique per row (a natural key) or "
            "to repeat (a foreign key / attribute) — judged from the role, not measured"
        ),
    )


class ColumnSemanticsOutput(BaseModel):
    columns: list[ColumnRoleProposal] = Field(
        ..., description="One entry per classified column across all source files"
    )
