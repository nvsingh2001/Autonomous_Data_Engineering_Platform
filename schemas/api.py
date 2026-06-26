from pydantic import BaseModel


class RunRequest(BaseModel):
    instructions: str = ""


class QueryRequest(BaseModel):
    question: str = ""


class ApprovalInput(BaseModel):
    approved: bool
