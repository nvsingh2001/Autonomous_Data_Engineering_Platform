from typing import Literal
from pydantic import BaseModel, Field


class QuestionVerdict(BaseModel):
    question: str = Field(..., description="A single business question drawn from the request")
    verdict: Literal["answerable", "partial", "unanswerable"] = Field(
        ...,
        description="Whether the available source data can answer this question",
    )
    reason: str = Field(
        ...,
        description="Short justification naming the columns/entities present or missing",
    )


class AnswerabilityOutput(BaseModel):
    verdicts: list[QuestionVerdict] = Field(
        ...,
        description="One verdict per distinct business question found in the request",
    )
    summary: str = Field(
        ...,
        description="One-paragraph overall assessment of how well the data supports the request",
    )
