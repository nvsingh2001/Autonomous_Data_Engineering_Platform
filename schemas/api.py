from pydantic import BaseModel


class RunRequest(BaseModel):
    instructions: str = ""
    questions: list[str] = []
    domain: str = "e-commerce"
    priority_metrics: list[str] = []
    metric_definitions: list[dict] = []
    decision_context: str = ""


class IntentMessageRequest(BaseModel):
    message: str = ""


class QueryRequest(BaseModel):
    question: str = ""


class ApprovalInput(BaseModel):
    approved: bool
