import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import make_asgi_app
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.staticfiles import StaticFiles

from hireflow.auth import Principal, create_access_token, current_principal, require_roles
from hireflow.availability import (
    add_candidate_availability,
    add_interviewer_availability,
    suggest_earliest_slot,
)
from hireflow.dashboard import dashboard_overview
from hireflow.db import engine, get_session
from hireflow.models import Base, Candidate, Interview, Job, Notification, NotificationInbox, User
from hireflow.notifications import request_replay
from hireflow.observability import begin_request, finish_request
from hireflow.rate_limit import enforce_rate_limit
from hireflow.recruiting import (
    DuplicateResource,
    InvalidReference,
    TenantNotFound,
    authenticate,
    bootstrap_tenant,
    create_application,
    create_candidate,
    create_job,
    create_user,
    list_candidates,
    list_jobs,
    list_users,
)
from hireflow.schemas import (
    AccessTokenRead,
    ApplicationCreate,
    ApplicationRead,
    AuditRead,
    AvailabilityCreate,
    AvailabilityRead,
    CandidateCreate,
    CandidateRead,
    DashboardOverviewRead,
    InterviewCreate,
    InterviewRead,
    InterviewStatusUpdate,
    JobCreate,
    JobRead,
    LoginRequest,
    NotificationFailureRead,
    NotificationRead,
    ScheduleSuggestionRead,
    ScheduleSuggestionRequest,
    TenantBootstrap,
    UserCreate,
    UserRead,
    UserRole,
)
from hireflow.service import (
    InvalidTransition,
    SchedulingConflict,
    StaleVersion,
    create_interview,
    list_audits,
    transition_interview,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema evolution is migration-only. This escape hatch is for isolated local experiments.
    if os.getenv("HIREFLOW_BOOTSTRAP_SCHEMA") == "true":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    app.state.redis = Redis.from_url(os.getenv("HIREFLOW_REDIS_URL", "redis://localhost:6379/0"))
    try:
        yield
    finally:
        await app.state.redis.aclose()


app = FastAPI(
    title="HireFlow",
    version="0.1.0",
    description="Multi-tenant interview scheduling and collaboration platform",
    lifespan=lifespan,
)
app.mount("/metrics", make_asgi_app())
web_root = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=web_root), name="assets")


@app.middleware("http")
async def observe_requests(request, call_next):
    request_id, token, started_at = begin_request(request.headers.get("X-Request-ID"))
    response = None
    try:
        response = await call_next(request)
        return response
    except Exception:
        response = JSONResponse(status_code=500, content={"detail": "internal server error"})
        raise
    finally:
        status_code = response.status_code if response is not None else 500
        finish_request(
            request_id=request_id,
            token=token,
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            started_at=started_at,
        )
        if response is not None:
            response.headers["X-Request-ID"] = request_id


@app.middleware("http")
async def rate_limit_requests(request, call_next):
    return await enforce_rate_limit(request, call_next)


def current_tenant(principal: Principal = Depends(current_principal)) -> UUID:
    return principal.tenant_id


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def workbench() -> FileResponse:
    return FileResponse(web_root / "index.html")


@app.get("/v1/dashboard/overview", response_model=DashboardOverviewRead)
async def get_dashboard_overview(
    tenant_id: UUID = Depends(current_tenant), session: AsyncSession = Depends(get_session)
) -> DashboardOverviewRead:
    return await dashboard_overview(session, tenant_id)


@app.post("/v1/auth/bootstrap", response_model=AccessTokenRead, status_code=status.HTTP_201_CREATED)
async def bootstrap(payload: TenantBootstrap, session: AsyncSession = Depends(get_session)):
    try:
        owner = await bootstrap_tenant(session, payload)
        return AccessTokenRead(
            access_token=create_access_token(owner.id, owner.tenant_id, owner.role)
        )
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/auth/tokens", response_model=AccessTokenRead)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await authenticate(session, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return AccessTokenRead(access_token=create_access_token(user.id, user.tenant_id, user.role))


@app.post("/v1/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await create_user(session, tenant_id, payload)
    except TenantNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/users", response_model=list[UserRead])
async def get_users(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    return await list_users(session, tenant_id)


@app.post("/v1/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def register_job(
    payload: JobCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await create_job(session, tenant_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/v1/jobs", response_model=list[JobRead])
async def get_jobs(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[Job]:
    return await list_jobs(session, tenant_id)


@app.post("/v1/candidates", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def register_candidate(
    payload: CandidateCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await create_candidate(session, tenant_id, payload)
    except TenantNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/candidates", response_model=list[CandidateRead])
async def get_candidates(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> list[Candidate]:
    return await list_candidates(session, tenant_id)


@app.post("/v1/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
async def submit_application(
    payload: ApplicationCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await create_application(session, tenant_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post(
    "/v1/candidates/{candidate_id}/availability",
    response_model=AvailabilityRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_candidate_availability(
    candidate_id: UUID,
    payload: AvailabilityCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await add_candidate_availability(session, tenant_id, candidate_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post(
    "/v1/interviewers/{interviewer_id}/availability",
    response_model=AvailabilityRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_interviewer_availability(
    interviewer_id: UUID,
    payload: AvailabilityCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER)
    ),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await add_interviewer_availability(session, tenant_id, interviewer_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/scheduling/suggestions", response_model=ScheduleSuggestionRead)
async def scheduling_suggestion(
    payload: ScheduleSuggestionRequest,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
):
    try:
        slot = await suggest_earliest_slot(
            session, tenant_id, payload.candidate_id, payload.interviewer_id, payload.duration_minutes
        )
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if slot is None:
        raise HTTPException(status_code=404, detail="no shared availability for this duration")
    return ScheduleSuggestionRead(starts_at=slot.starts_at, ends_at=slot.ends_at)


@app.post("/v1/interviews", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
async def schedule_interview(
    payload: InterviewCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
) -> Interview:
    try:
        return await create_interview(session, tenant_id, payload)
    except (SchedulingConflict, InvalidReference) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/interviews/{interview_id}/status", response_model=InterviewRead)
async def update_status(
    interview_id: UUID,
    payload: InterviewStatusUpdate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER)
    ),
    session: AsyncSession = Depends(get_session),
) -> Interview:
    try:
        return await transition_interview(session, tenant_id, interview_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except StaleVersion as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/interviews/{interview_id}/audit", response_model=list[AuditRead])
async def interview_audit(
    interview_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
):
    return await list_audits(session, tenant_id, interview_id)


@app.get("/v1/interviews/{interview_id}", response_model=InterviewRead)
async def get_interview(
    interview_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER)
    ),
    session: AsyncSession = Depends(get_session),
) -> Interview:
    interview = await session.scalar(
        select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
    )
    if interview is None:
        raise HTTPException(status_code=404, detail="interview not found")
    return interview


@app.get("/v1/notifications", response_model=list[NotificationRead])
async def list_notifications(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.scalars(
        select(Notification)
        .where(Notification.tenant_id == tenant_id)
        .order_by(Notification.created_at.desc())
    )
    return list(rows)


@app.get("/v1/admin/notification-failures", response_model=list[NotificationFailureRead])
async def list_notification_failures(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(get_session),
):
    rows = await session.scalars(
        select(NotificationInbox)
        .where(NotificationInbox.tenant_id == tenant_id, NotificationInbox.status == "failed")
        .order_by(NotificationInbox.created_at.desc())
    )
    return list(rows)


@app.post("/v1/admin/notification-failures/{event_id}/replay", response_model=NotificationFailureRead)
async def replay_notification_failure(
    event_id: str,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(get_session),
):
    inbox = await request_replay(session, tenant_id, event_id)
    if inbox is None:
        raise HTTPException(status_code=404, detail="failed notification event not found")
    return inbox
