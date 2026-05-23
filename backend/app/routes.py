from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field


router = APIRouter()


class ApiStatus(BaseModel):
    status: str
    message: str
    timestamp: datetime


class LeadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    company: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=1000)


class LeadResponse(BaseModel):
    id: str
    status: str
    received_at: datetime


@router.get("/status", response_model=ApiStatus, tags=["api"])
async def status() -> ApiStatus:
    return ApiStatus(
        status="ready",
        message="FastAPI backend is ready for the Lovable UI.",
        timestamp=datetime.now(timezone.utc),
    )


@router.post("/leads", response_model=LeadResponse, status_code=201, tags=["leads"])
async def create_lead(lead: LeadCreate) -> LeadResponse:
    # Replace this with persistence or CRM handoff once the hackathon flow is set.
    return LeadResponse(
        id=str(uuid4()),
        status="received",
        received_at=datetime.now(timezone.utc),
    )
