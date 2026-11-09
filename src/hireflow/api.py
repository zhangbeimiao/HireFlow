import asyncio
import hmac
import os
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import make_asgi_app
from redis.asyncio import Redis
from sqlalchemy import select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from hireflow.auth import (
    Principal,
    access_token_minutes,
    create_access_token,
    current_principal,
    require_roles,
)
from hireflow.auth_tokens import (
    InvalidRefreshToken,
    issue_refresh_token,
    refresh_token_days,
    revoke_refresh_token,
    rotate_refresh_token,
)
from hireflow.availability import (
    add_candidate_availability,
    add_interviewer_availability,
    suggest_earliest_slot,
)
from hireflow.cache import cached_list, invalidate_cached_list
from hireflow.dashboard import dashboard_overview
from hireflow.db import engine, get_session, get_tenant_session, tenant_session_scope
from hireflow.domain import ApplicationStage, InterviewStatus
from hireflow.health import postgres_ready, readiness_response, redis_ready
from hireflow.models import (
    Base,
    Interview,
    Notification,
    NotificationDelivery,
    NotificationInbox,
    User,
)
from hireflow.notifications import request_replay
from hireflow.observability import SCHEDULING_CONFLICTS, begin_request, finish_request
from hireflow.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidCursor,
    PageCursor,
    decode_cursor,
    encode_cursor,
)
from hireflow.pipeline import (
    PipelineInvalidTransition,
    PipelineStaleVersion,
    ScorecardLocked,
    create_interview_round,
    create_offer,
    list_application_audits,
    list_applications,
    list_interview_rounds,
    list_offer_audits,
    list_offers,
    list_scorecards,
    transition_application,
    transition_offer,
    upsert_scorecard,
)
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
    update_candidate,
    update_job,
)
from hireflow.schemas import (
    AccessTokenRead,
    ApplicationAuditRead,
    ApplicationCreate,
    ApplicationRead,
    ApplicationStageUpdate,
    AuditRead,
    AvailabilityCreate,
    AvailabilityRead,
    CandidateCreate,
    CandidateRead,
    CandidateUpdate,
    DashboardInterviewRead,
    DashboardOverviewRead,
    InterviewCreate,
    InterviewRead,
    InterviewReschedule,
    InterviewRoundCreate,
    InterviewRoundRead,
    InterviewStatusUpdate,
    JobCreate,
    JobRead,
    JobUpdate,
    LoginRequest,
    NotificationDeliveryRead,
    NotificationFailureRead,
    NotificationRead,
    OfferAuditRead,
    OfferCreate,
    OfferRead,
    OfferStatusUpdate,
    RefreshTokenRequest,
    ScheduleSuggestionRead,
    ScheduleSuggestionRequest,
    ScorecardRead,
    ScorecardUpsert,
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
    list_interviews,
    reschedule_interview,
    transition_interview,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema evolution is migration-only. This escape hatch is for isolated local experiments.
    if os.getenv("HIREFLOW_BOOTSTRAP_SCHEMA") == "true":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    app.state.redis = Redis.from_url(os.getenv("HIREFLOW_REDIS_URL", "redis://localhost:6379/0"))
    app.state.cache_ttl_seconds = int(os.getenv("HIREFLOW_LIST_CACHE_TTL_SECONDS", "60"))
    if app.state.cache_ttl_seconds < 1:
        raise RuntimeError("HIREFLOW_LIST_CACHE_TTL_SECONDS must be at least 1")
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
allowed_hosts = [
    host.strip()
    for host in os.getenv("HIREFLOW_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
    if host.strip()
]
if not allowed_hosts:
    raise RuntimeError("HIREFLOW_ALLOWED_HOSTS must contain at least one host")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.mount("/metrics", make_asgi_app())
web_root = Path(__file__).parent / "web"
app.mount("/assets", StaticFiles(directory=web_root), name="assets")

REFRESH_COOKIE = "hireflow_refresh"
CSRF_COOKIE = "hireflow_csrf"


def _cookie_secure() -> bool:
    value = os.getenv("HIREFLOW_COOKIE_SECURE", "false").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError("HIREFLOW_COOKIE_SECURE must be true or false")
    return value == "true"


def _set_session_cookies(response: Response, refresh_token: str) -> None:
    max_age = refresh_token_days() * 24 * 60 * 60
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        path="/v1/auth",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=_cookie_secure(),
        samesite="strict",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _browser_session(request: Request) -> bool:
    return request.headers.get("X-HireFlow-Client") == "browser"


def _refresh_credential(
    request: Request, payload: RefreshTokenRequest | None
) -> tuple[str, bool]:
    if payload is not None:
        return payload.refresh_token, False
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise HTTPException(status_code=401, detail="missing refresh token")
    csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
    csrf_header = request.headers.get("X-CSRF-Token", "")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="invalid CSRF token")
    return refresh_token, True


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https://fastapi.tiangolo.com; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


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


async def tenant_session(principal: Principal = Depends(current_principal)):
    async for session in get_tenant_session(principal.tenant_id):
        yield session


def _decode_page_cursor(value: str | None) -> PageCursor | None:
    if value is None:
        return None
    try:
        return decode_cursor(value)
    except InvalidCursor as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _page_value(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item[field]
    return getattr(item, field)


def _page_response(
    items: list[Any], limit: int, response: Response, *, timestamp_field: str = "created_at"
) -> list[Any]:
    page = items[:limit]
    response.headers["X-Page-Limit"] = str(limit)
    if len(items) > limit and page:
        last = page[-1]
        timestamp = _page_value(last, timestamp_field)
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        response.headers["X-Next-Cursor"] = encode_cursor(
            timestamp, UUID(str(_page_value(last, "id")))
        )
    return page


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    postgres_ok, redis_ok = await asyncio.gather(
        postgres_ready(), redis_ready(request.app.state.redis)
    )
    status_code, payload = readiness_response(postgres_ok, redis_ok)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/", include_in_schema=False)
async def workbench() -> FileResponse:
    return FileResponse(web_root / "index.html")


@app.get("/app.css", include_in_schema=False)
async def workbench_styles() -> FileResponse:
    """Serve relative assets so index.html also works as a local static preview."""
    return FileResponse(web_root / "app.css")


@app.get("/operations.css", include_in_schema=False)
async def workbench_operation_styles() -> FileResponse:
    return FileResponse(web_root / "operations.css")


@app.get("/app.js", include_in_schema=False)
async def workbench_script() -> FileResponse:
    return FileResponse(web_root / "app.js")


@app.get("/operations.js", include_in_schema=False)
async def workbench_operation_script() -> FileResponse:
    return FileResponse(web_root / "operations.js")


@app.get("/v1/dashboard/overview", response_model=DashboardOverviewRead)
async def get_dashboard_overview(
    tenant_id: UUID = Depends(current_tenant), session: AsyncSession = Depends(tenant_session)
) -> DashboardOverviewRead:
    return await dashboard_overview(session, tenant_id)


@app.post("/v1/auth/bootstrap", response_model=AccessTokenRead, status_code=status.HTTP_201_CREATED)
async def bootstrap(
    payload: TenantBootstrap,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    try:
        owner = await bootstrap_tenant(session, payload)
        refresh_token = await issue_refresh_token(session, owner)
        _set_session_cookies(response, refresh_token)
        return AccessTokenRead(
            access_token=create_access_token(owner.id, owner.tenant_id, owner.role),
            refresh_token=None if _browser_session(request) else refresh_token,
            expires_in=access_token_minutes() * 60,
        )
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/auth/tokens", response_model=AccessTokenRead)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    user = await authenticate(session, payload)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    refresh_token = await issue_refresh_token(session, user)
    _set_session_cookies(response, refresh_token)
    return AccessTokenRead(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=None if _browser_session(request) else refresh_token,
        expires_in=access_token_minutes() * 60,
    )


@app.post("/v1/auth/refresh", response_model=AccessTokenRead)
async def refresh_access_token(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> AccessTokenRead:
    raw_token, cookie_session = _refresh_credential(request, payload)
    try:
        user, refresh_token = await rotate_refresh_token(session, raw_token)
    except InvalidRefreshToken as error:
        raise HTTPException(status_code=401, detail="invalid refresh token") from error
    _set_session_cookies(response, refresh_token)
    return AccessTokenRead(
        access_token=create_access_token(user.id, user.tenant_id, user.role),
        refresh_token=None if cookie_session else refresh_token,
        expires_in=access_token_minutes() * 60,
    )


@app.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    payload: RefreshTokenRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> None:
    raw_token, _ = _refresh_credential(request, payload)
    try:
        await revoke_refresh_token(session, raw_token)
    except InvalidRefreshToken as error:
        raise HTTPException(status_code=401, detail="invalid refresh token") from error
    _clear_session_cookies(response)


@app.post("/v1/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: UserCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(tenant_session),
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
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
) -> list[User]:
    return await list_users(session, tenant_id)


@app.post("/v1/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def register_job(
    payload: JobCreate,
    request: Request,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        job = await create_job(session, tenant_id, payload)
        await invalidate_cached_list(request.app.state.redis, resource="jobs", tenant_id=tenant_id)
        return job
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/v1/jobs", response_model=list[JobRead])
async def get_jobs(
    request: Request,
    response: Response,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
) -> list[JobRead]:
    page_cursor = _decode_page_cursor(cursor)

    async def load_jobs():
        async with tenant_session_scope(tenant_id) as session:
            rows = await list_jobs(
                session,
                tenant_id,
                limit=limit,
                cursor=page_cursor,
                query_text=q,
                status=status_filter,
            )
            return [
                JobRead.model_validate(row, from_attributes=True).model_dump(mode="json")
                for row in rows
            ]

    result = await cached_list(
        request.app.state.redis,
        resource="jobs",
        tenant_id=tenant_id,
        schema=JobRead,
        loader=load_jobs,
        ttl_seconds=request.app.state.cache_ttl_seconds,
        bypass=(
            "no-cache" in request.headers.get("Cache-Control", "").lower()
            or page_cursor is not None
            or limit != DEFAULT_PAGE_SIZE
            or q is not None
            or status_filter is not None
        ),
    )
    response.headers["X-Cache"] = result.outcome
    return _page_response(result.items, limit, response)


@app.patch("/v1/jobs/{job_id}", response_model=JobRead)
async def revise_job(
    job_id: UUID,
    payload: JobUpdate,
    request: Request,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        job = await update_job(session, tenant_id, job_id, payload)
        await invalidate_cached_list(request.app.state.redis, resource="jobs", tenant_id=tenant_id)
        return job
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/candidates", response_model=CandidateRead, status_code=status.HTTP_201_CREATED)
async def register_candidate(
    payload: CandidateCreate,
    request: Request,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        candidate = await create_candidate(session, tenant_id, payload)
        await invalidate_cached_list(
            request.app.state.redis, resource="candidates", tenant_id=tenant_id
        )
        return candidate
    except TenantNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/candidates", response_model=list[CandidateRead])
async def get_candidates(
    request: Request,
    response: Response,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
) -> list[CandidateRead]:
    page_cursor = _decode_page_cursor(cursor)

    async def load_candidates():
        async with tenant_session_scope(tenant_id) as session:
            rows = await list_candidates(
                session, tenant_id, limit=limit, cursor=page_cursor, query_text=q
            )
            return [
                CandidateRead.model_validate(row, from_attributes=True).model_dump(mode="json")
                for row in rows
            ]

    result = await cached_list(
        request.app.state.redis,
        resource="candidates",
        tenant_id=tenant_id,
        schema=CandidateRead,
        loader=load_candidates,
        ttl_seconds=request.app.state.cache_ttl_seconds,
        bypass=(
            "no-cache" in request.headers.get("Cache-Control", "").lower()
            or page_cursor is not None
            or limit != DEFAULT_PAGE_SIZE
            or q is not None
        ),
    )
    response.headers["X-Cache"] = result.outcome
    return _page_response(result.items, limit, response)


@app.patch("/v1/candidates/{candidate_id}", response_model=CandidateRead)
async def revise_candidate(
    candidate_id: UUID,
    payload: CandidateUpdate,
    request: Request,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        candidate = await update_candidate(session, tenant_id, candidate_id, payload)
        await invalidate_cached_list(
            request.app.state.redis, resource="candidates", tenant_id=tenant_id
        )
        return candidate
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
async def submit_application(
    payload: ApplicationCreate,
    tenant_id: UUID = Depends(current_tenant),
    principal: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await create_application(session, tenant_id, principal.user_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/applications", response_model=list[ApplicationRead])
async def get_applications(
    response: Response,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, min_length=1, max_length=120),
    stage: ApplicationStage | None = Query(default=None),
    job_id: UUID | None = Query(default=None),
    candidate_id: UUID | None = Query(default=None),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    rows = await list_applications(
        session,
        tenant_id,
        limit=limit,
        cursor=_decode_page_cursor(cursor),
        query_text=q,
        stage=stage,
        job_id=job_id,
        candidate_id=candidate_id,
    )
    return _page_response(rows, limit, response)


@app.post("/v1/applications/{application_id}/stage", response_model=ApplicationRead)
async def update_application_stage(
    application_id: UUID,
    payload: ApplicationStageUpdate,
    tenant_id: UUID = Depends(current_tenant),
    principal: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await transition_application(
            session, tenant_id, principal.user_id, application_id, payload
        )
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PipelineInvalidTransition, PipelineStaleVersion) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/applications/{application_id}/audit", response_model=list[ApplicationAuditRead])
async def application_audit(
    application_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_application_audits(session, tenant_id, application_id)


@app.post(
    "/v1/jobs/{job_id}/rounds",
    response_model=InterviewRoundRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_interview_round(
    job_id: UUID,
    payload: InterviewRoundCreate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await create_interview_round(session, tenant_id, job_id, payload)
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except DuplicateResource as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/jobs/{job_id}/rounds", response_model=list[InterviewRoundRead])
async def get_interview_rounds(
    job_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_interview_rounds(session, tenant_id, job_id)


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
    session: AsyncSession = Depends(tenant_session),
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
        require_roles(
            UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER
        )
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await add_interviewer_availability(session, tenant_id, interviewer_id, payload)
    except (TenantNotFound, InvalidReference) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/scheduling/suggestions", response_model=ScheduleSuggestionRead)
async def scheduling_suggestion(
    payload: ScheduleSuggestionRequest,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        slot = await suggest_earliest_slot(
            session,
            tenant_id,
            payload.candidate_id,
            payload.interviewer_id,
            payload.duration_minutes,
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
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
) -> Interview:
    try:
        return await create_interview(session, tenant_id, payload)
    except (SchedulingConflict, InvalidReference) as error:
        if isinstance(error, SchedulingConflict):
            SCHEDULING_CONFLICTS.inc()
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/interviews", response_model=list[DashboardInterviewRead])
async def get_interviews(
    response: Response,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    starts_after: datetime | None = Query(default=None),
    starts_before: datetime | None = Query(default=None),
    status_filter: InterviewStatus | None = Query(default=None, alias="status"),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
) -> list[DashboardInterviewRead]:
    page_cursor = _decode_page_cursor(cursor)
    rows = await list_interviews(
        session,
        tenant_id,
        limit=limit,
        cursor=page_cursor,
        starts_after=starts_after,
        starts_before=starts_before,
        status=status_filter,
    )
    return _page_response(rows, limit, response, timestamp_field="starts_at")


@app.post("/v1/interviews/{interview_id}/status", response_model=InterviewRead)
async def update_status(
    interview_id: UUID,
    payload: InterviewStatusUpdate,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(
            UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER
        )
    ),
    session: AsyncSession = Depends(tenant_session),
) -> Interview:
    try:
        return await transition_interview(session, tenant_id, interview_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidTransition as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except StaleVersion as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/interviews/{interview_id}/reschedule", response_model=InterviewRead)
async def reschedule(
    interview_id: UUID,
    payload: InterviewReschedule,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
) -> Interview:
    try:
        return await reschedule_interview(session, tenant_id, interview_id, payload)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (InvalidTransition, SchedulingConflict, StaleVersion) as error:
        if isinstance(error, SchedulingConflict):
            SCHEDULING_CONFLICTS.inc()
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/interviews/{interview_id}/audit", response_model=list[AuditRead])
async def interview_audit(
    interview_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_audits(session, tenant_id, interview_id)


@app.get("/v1/interviews/{interview_id}", response_model=InterviewRead)
async def get_interview(
    interview_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(
            UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER, UserRole.INTERVIEWER
        )
    ),
    session: AsyncSession = Depends(tenant_session),
) -> Interview:
    interview = await session.scalar(
        select(Interview).where(Interview.id == interview_id, Interview.tenant_id == tenant_id)
    )
    if interview is None:
        raise HTTPException(status_code=404, detail="interview not found")
    return interview


@app.put("/v1/interviews/{interview_id}/scorecard", response_model=ScorecardRead)
async def save_scorecard(
    interview_id: UUID,
    payload: ScorecardUpsert,
    tenant_id: UUID = Depends(current_tenant),
    principal: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.INTERVIEWER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await upsert_scorecard(session, tenant_id, principal.user_id, interview_id, payload)
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ScorecardLocked as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/interviews/{interview_id}/scorecards", response_model=list[ScorecardRead])
async def get_scorecards(
    interview_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_scorecards(session, tenant_id, interview_id)


@app.post("/v1/offers", response_model=OfferRead, status_code=status.HTTP_201_CREATED)
async def draft_offer(
    payload: OfferCreate,
    tenant_id: UUID = Depends(current_tenant),
    principal: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await create_offer(session, tenant_id, principal.user_id, payload)
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (DuplicateResource, PipelineInvalidTransition) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/offers", response_model=list[OfferRead])
async def get_offers(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_offers(session, tenant_id)


@app.post("/v1/offers/{offer_id}/status", response_model=OfferRead)
async def update_offer_status(
    offer_id: UUID,
    payload: OfferStatusUpdate,
    tenant_id: UUID = Depends(current_tenant),
    principal: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER)),
    session: AsyncSession = Depends(tenant_session),
):
    try:
        return await transition_offer(session, tenant_id, principal.user_id, offer_id, payload)
    except InvalidReference as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (PipelineInvalidTransition, PipelineStaleVersion) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/offers/{offer_id}/audit", response_model=list[OfferAuditRead])
async def get_offer_audit(
    offer_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)),
    session: AsyncSession = Depends(tenant_session),
):
    return await list_offer_audits(session, tenant_id, offer_id)


@app.get("/v1/notifications", response_model=list[NotificationRead])
async def list_notifications(
    response: Response,
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=512),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    page_cursor = _decode_page_cursor(cursor)
    query = select(Notification).where(Notification.tenant_id == tenant_id)
    if page_cursor is not None:
        query = query.where(
            tuple_(Notification.created_at, Notification.id)
            < (page_cursor.created_at, page_cursor.item_id)
        )
    rows = await session.scalars(
        query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1)
    )
    return _page_response(list(rows), limit, response)


@app.patch("/v1/notifications/{notification_id}/read", response_model=NotificationRead)
async def mark_notification_read(
    notification_id: UUID,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    notification = await session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.tenant_id == tenant_id
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="notification not found")
    if notification.read_at is None:
        notification.read_at = datetime.now(UTC)
        await session.commit()
    return notification


@app.post("/v1/notifications/read-all")
async def mark_all_notifications_read(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(
        require_roles(UserRole.OWNER, UserRole.RECRUITER, UserRole.HIRING_MANAGER)
    ),
    session: AsyncSession = Depends(tenant_session),
):
    result = await session.execute(
        update(Notification)
        .where(Notification.tenant_id == tenant_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    await session.commit()
    return {"updated": result.rowcount or 0}


@app.get("/v1/admin/notification-failures", response_model=list[NotificationFailureRead])
async def list_notification_failures(
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(tenant_session),
):
    rows = await session.scalars(
        select(NotificationInbox)
        .where(NotificationInbox.tenant_id == tenant_id, NotificationInbox.status == "failed")
        .order_by(NotificationInbox.created_at.desc())
    )
    return list(rows)


@app.get(
    "/v1/admin/notification-deliveries", response_model=list[NotificationDeliveryRead]
)
async def list_notification_deliveries(
    status_filter: str | None = Query(default=None, alias="status", max_length=30),
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(tenant_session),
):
    query = select(NotificationDelivery).where(NotificationDelivery.tenant_id == tenant_id)
    if status_filter:
        query = query.where(NotificationDelivery.status == status_filter)
    rows = await session.scalars(
        query.order_by(NotificationDelivery.created_at.desc()).limit(100)
    )
    return list(rows)


@app.post(
    "/v1/admin/notification-failures/{event_id}/replay", response_model=NotificationFailureRead
)
async def replay_notification_failure(
    event_id: str,
    tenant_id: UUID = Depends(current_tenant),
    _: Principal = Depends(require_roles(UserRole.OWNER)),
    session: AsyncSession = Depends(tenant_session),
):
    inbox = await request_replay(session, tenant_id, event_id)
    if inbox is None:
        raise HTTPException(status_code=404, detail="failed notification event not found")
    return inbox
