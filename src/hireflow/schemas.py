from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

from hireflow.domain import InterviewStatus


class InterviewCreate(BaseModel):
    candidate_id: UUID
    interviewer_id: UUID
    job_id: UUID
    starts_at: datetime
    ends_at: datetime
    meeting_url: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_interval(self) -> "InterviewCreate":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("timestamps must include an offset, for example +08:00")
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class InterviewStatusUpdate(BaseModel):
    status: InterviewStatus
    version: int = Field(ge=1, description="Version read by the caller; protects against lost updates")
    reason: str | None = Field(default=None, max_length=500)


class InterviewRead(BaseModel):
    id: UUID
    tenant_id: UUID
    candidate_id: UUID
    interviewer_id: UUID
    job_id: UUID
    starts_at: datetime
    ends_at: datetime
    status: InterviewStatus
    version: int
    meeting_url: str | None


class AuditRead(BaseModel):
    id: UUID
    interview_id: UUID
    from_status: InterviewStatus | None
    to_status: InterviewStatus
    reason: str | None
    created_at: datetime


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class TenantRead(TenantCreate):
    id: UUID


class UserRole(StrEnum):
    OWNER = "owner"
    RECRUITER = "recruiter"
    INTERVIEWER = "interviewer"
    HIRING_MANAGER = "hiring_manager"


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    role: UserRole
    password: str = Field(min_length=12, max_length=128)


class UserRead(BaseModel):
    id: UUID
    tenant_id: UUID
    email: EmailStr
    display_name: str
    role: UserRole


class LoginRequest(BaseModel):
    tenant_id: UUID
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AccessTokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TenantBootstrap(BaseModel):
    tenant_name: str = Field(min_length=2, max_length=120)
    owner: UserCreate


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    hiring_manager_id: UUID | None = None


class JobUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    hiring_manager_id: UUID | None = None
    status: Literal["open", "paused", "closed"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> "JobUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one job field must be provided")
        for field in ("title", "status"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class JobRead(JobCreate):
    id: UUID
    tenant_id: UUID
    status: str


class CandidateCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=30)


class CandidateUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def require_change(self) -> "CandidateUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one candidate field must be provided")
        for field in ("email", "full_name"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class CandidateRead(CandidateCreate):
    id: UUID
    tenant_id: UUID


class ApplicationCreate(BaseModel):
    job_id: UUID
    candidate_id: UUID


class ApplicationRead(ApplicationCreate):
    id: UUID
    tenant_id: UUID
    stage: str


class AvailabilityCreate(BaseModel):
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def validate_interval(self) -> "AvailabilityCreate":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("timestamps must include an offset")
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        return self


class AvailabilityRead(AvailabilityCreate):
    id: UUID
    tenant_id: UUID


class ScheduleSuggestionRequest(BaseModel):
    candidate_id: UUID
    interviewer_id: UUID
    duration_minutes: int = Field(ge=15, le=240)


class ScheduleSuggestionRead(BaseModel):
    starts_at: datetime
    ends_at: datetime


class NotificationRead(BaseModel):
    id: UUID
    source_event_id: str
    recipient_type: str
    recipient_id: UUID
    title: str
    content: str
    created_at: datetime
    read_at: datetime | None


class NotificationFailureRead(BaseModel):
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime


class DashboardInterviewRead(BaseModel):
    id: UUID
    candidate_name: str
    interviewer_name: str
    job_title: str
    starts_at: datetime
    ends_at: datetime
    status: str


class DashboardOverviewRead(BaseModel):
    open_jobs: int
    candidates: int
    upcoming_interviews: int
    unread_notifications: int
    upcoming: list[DashboardInterviewRead]
