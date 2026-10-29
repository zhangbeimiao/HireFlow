"""Read model for the recruiter-facing workbench."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hireflow.models import Candidate, Interview, Job, Notification, User
from hireflow.schemas import DashboardInterviewRead, DashboardOverviewRead


async def dashboard_overview(session: AsyncSession, tenant_id: UUID) -> DashboardOverviewRead:
    now = datetime.now(UTC)

    async def count_rows(model, *conditions) -> int:
        value = await session.scalar(
            select(func.count()).select_from(model).where(model.tenant_id == tenant_id, *conditions)
        )
        return int(value or 0)

    open_jobs = await count_rows(Job, Job.status == "open")
    candidates = await count_rows(Candidate)
    upcoming_count = await count_rows(Interview, Interview.starts_at >= now)
    unread_notifications = await count_rows(Notification, Notification.read_at.is_(None))

    rows = await session.execute(
        select(Interview, Candidate.full_name, User.display_name, Job.title)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .join(User, User.id == Interview.interviewer_id)
        .join(Job, Job.id == Interview.job_id)
        .where(Interview.tenant_id == tenant_id, Interview.starts_at >= now)
        .order_by(Interview.starts_at)
        .limit(5)
    )
    interviews = [
        DashboardInterviewRead(
            id=interview.id,
            candidate_name=candidate_name,
            interviewer_name=interviewer_name,
            job_title=job_title,
            starts_at=interview.starts_at,
            ends_at=interview.ends_at,
            status=interview.status,
        )
        for interview, candidate_name, interviewer_name, job_title in rows
    ]

    return DashboardOverviewRead(
        open_jobs=open_jobs,
        candidates=candidates,
        upcoming_interviews=upcoming_count,
        unread_notifications=unread_notifications,
        upcoming=interviews,
    )
