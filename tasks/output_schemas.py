from pydantic import BaseModel, Field


class QualityOutput(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Quality score from 0 to 100")
    report: str = Field(..., description="Full markdown quality report")
