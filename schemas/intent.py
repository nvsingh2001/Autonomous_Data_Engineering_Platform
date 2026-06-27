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


class KPIDefinition(BaseModel):
    name: str = Field(..., description="Short metric name, e.g. 'Conversion rate'")
    description: str = Field(..., description="What the metric measures, in one line")


class BusinessIntent(BaseModel):
    """Structured intent captured from the pre-run conversation with the user."""

    questions: list[str] = Field(
        default_factory=list,
        description="The distinct business questions the warehouse must answer",
    )
    domain: str = Field(default="e-commerce", description="Business domain of the data")
    priority_metrics: list[str] = Field(
        default_factory=list,
        description="Metrics the user cares about most, e.g. ['Revenue', 'AOV']",
    )
    decision_context: str = Field(
        default="",
        description="What decision the analysis will inform, if stated",
    )

    def to_instructions(self) -> str:
        """Render to the plain-text form the existing pipeline consumes as
        user_instructions, so Phase 1/2/3 steps need no changes."""
        lines: list[str] = []
        if self.questions:
            lines.extend(f"{i}. {q}" for i, q in enumerate(self.questions, 1))
        if self.priority_metrics:
            lines.append("Priority metrics: " + ", ".join(self.priority_metrics))
        if self.decision_context:
            lines.append("Decision context: " + self.decision_context)
        return "\n".join(lines)
