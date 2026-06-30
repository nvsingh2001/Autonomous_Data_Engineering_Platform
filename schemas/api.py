from pydantic import BaseModel


class RunRequest(BaseModel):
    instructions: str = ""
    # Structured intent from the conversational intake (optional; takes precedence
    # over `instructions` when `questions` is non-empty).
    questions: list[str] = []
    domain: str = "e-commerce"
    priority_metrics: list[str] = []
    # Each item: {"name": <metric>, "definition": <precise operational definition>}.
    metric_definitions: list[dict] = []
    decision_context: str = ""


class IntentMessageRequest(BaseModel):
    message: str = ""


class QueryRequest(BaseModel):
    question: str = ""


class ApprovalInput(BaseModel):
    approved: bool
