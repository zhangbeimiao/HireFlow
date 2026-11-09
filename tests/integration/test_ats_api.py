"""Black-box ATS API tests against a running HireFlow stack.

The regular unit-test job skips this module.  Docker CI sets
``HIREFLOW_INTEGRATION_BASE_URL`` after the full stack is healthy so these
tests exercise authentication, PostgreSQL RLS, state machines and transaction
boundaries through the public HTTP API.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

BASE_URL = os.getenv("HIREFLOW_INTEGRATION_BASE_URL")
PASSWORD = "HireFlowIntegrationPassword!2026"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not BASE_URL,
        reason="set HIREFLOW_INTEGRATION_BASE_URL to run Docker API integration tests",
    ),
]


@pytest.fixture
def client() -> httpx.Client:
    with httpx.Client(base_url=BASE_URL or "http://127.0.0.1:8000", timeout=15.0) as value:
        yield value


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    expected: int,
    headers: dict[str, str] | None = None,
    json: dict | None = None,
) -> httpx.Response:
    response = client.request(method, path, headers=headers, json=json)
    assert response.status_code == expected, response.text
    return response


def _bootstrap(client: httpx.Client, label: str) -> dict[str, str]:
    suffix = f"{label}-{uuid4().hex[:10]}"
    response = _request(
        client,
        "POST",
        "/v1/auth/bootstrap",
        expected=201,
        json={
            "tenant_name": f"ATS integration {suffix}",
            "owner": {
                "email": f"owner.{suffix}@hireflow.demo",
                "display_name": "ATS Integration Owner",
                "role": "owner",
                "password": PASSWORD,
            },
        },
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _new_application(client: httpx.Client, label: str) -> dict:
    headers = _bootstrap(client, label)
    job = _request(
        client,
        "POST",
        "/v1/jobs",
        expected=201,
        headers=headers,
        json={"title": f"Backend Engineer {label}"},
    ).json()
    candidate = _request(
        client,
        "POST",
        "/v1/candidates",
        expected=201,
        headers=headers,
        json={
            "email": f"candidate.{label}.{uuid4().hex[:10]}@hireflow.demo",
            "full_name": "API Candidate",
        },
    ).json()
    application = _request(
        client,
        "POST",
        "/v1/applications",
        expected=201,
        headers=headers,
        json={"job_id": job["id"], "candidate_id": candidate["id"]},
    ).json()
    return {
        "headers": headers,
        "job": job,
        "candidate": candidate,
        "application": application,
    }


def _advance(client: httpx.Client, context: dict, stage: str) -> dict:
    application = context["application"]
    response = _request(
        client,
        "POST",
        f"/v1/applications/{application['id']}/stage",
        expected=200,
        headers=context["headers"],
        json={"stage": stage, "version": application["version"], "reason": "API integration"},
    )
    context["application"] = response.json()
    return context["application"]


def test_application_state_machine_audit_and_optimistic_lock(client: httpx.Client) -> None:
    context = _new_application(client, "state")
    application = context["application"]

    _request(
        client,
        "POST",
        f"/v1/applications/{application['id']}/stage",
        expected=409,
        headers=context["headers"],
        json={"stage": "offer", "version": application["version"]},
    )

    original_version = application["version"]
    screening = _advance(client, context, "screening")
    _request(
        client,
        "POST",
        f"/v1/applications/{application['id']}/stage",
        expected=409,
        headers=context["headers"],
        json={"stage": "interview", "version": original_version},
    )
    interview = _advance(client, context, "interview")

    audits = _request(
        client,
        "GET",
        f"/v1/applications/{application['id']}/audit",
        expected=200,
        headers=context["headers"],
    ).json()
    assert screening["version"] == original_version + 1
    assert interview["version"] == original_version + 2
    assert [item["to_stage"] for item in audits] == ["applied", "screening", "interview"]


def test_scorecard_is_immutable_after_submission(client: httpx.Client) -> None:
    context = _new_application(client, "scorecard")
    interviewer_email = f"interviewer.{uuid4().hex[:10]}@hireflow.demo"
    interviewer = _request(
        client,
        "POST",
        "/v1/users",
        expected=201,
        headers=context["headers"],
        json={
            "email": interviewer_email,
            "display_name": "API Interviewer",
            "role": "interviewer",
            "password": PASSWORD,
        },
    ).json()
    token = _request(
        client,
        "POST",
        "/v1/auth/tokens",
        expected=200,
        json={
            "tenant_id": interviewer["tenant_id"],
            "email": interviewer_email,
            "password": PASSWORD,
        },
    ).json()["access_token"]
    interviewer_headers = {"Authorization": f"Bearer {token}"}

    round_ = _request(
        client,
        "POST",
        f"/v1/jobs/{context['job']['id']}/rounds",
        expected=201,
        headers=context["headers"],
        json={
            "name": "System Design",
            "sequence": 1,
            "duration_minutes": 60,
            "criteria": [{"key": "reliability", "label": "Reliability"}],
        },
    ).json()
    _advance(client, context, "screening")
    _advance(client, context, "interview")
    starts_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=3)
    interview = _request(
        client,
        "POST",
        "/v1/interviews",
        expected=201,
        headers=context["headers"],
        json={
            "candidate_id": context["candidate"]["id"],
            "interviewer_id": interviewer["id"],
            "job_id": context["job"]["id"],
            "application_id": context["application"]["id"],
            "round_id": round_["id"],
            "starts_at": starts_at.isoformat(),
            "ends_at": (starts_at + timedelta(hours=1)).isoformat(),
        },
    ).json()

    draft = _request(
        client,
        "PUT",
        f"/v1/interviews/{interview['id']}/scorecard",
        expected=200,
        headers=interviewer_headers,
        json={
            "recommendation": "hire",
            "overall_score": 4,
            "summary": "Good evidence",
            "answers": {"reliability": 4},
            "submit": False,
        },
    ).json()
    submitted = _request(
        client,
        "PUT",
        f"/v1/interviews/{interview['id']}/scorecard",
        expected=200,
        headers=interviewer_headers,
        json={
            "recommendation": "strong_hire",
            "overall_score": 5,
            "summary": "Ready for the role",
            "answers": {"reliability": 5},
            "submit": True,
        },
    ).json()
    _request(
        client,
        "PUT",
        f"/v1/interviews/{interview['id']}/scorecard",
        expected=409,
        headers=interviewer_headers,
        json={"recommendation": "no_hire", "overall_score": 1, "submit": False},
    )

    assert draft["submitted_at"] is None
    assert submitted["id"] == draft["id"]
    assert submitted["submitted_at"] is not None


def test_offer_acceptance_hires_application_and_preserves_audits(client: httpx.Client) -> None:
    context = _new_application(client, "offer")
    _advance(client, context, "screening")
    _advance(client, context, "interview")
    _advance(client, context, "offer")
    offer = _request(
        client,
        "POST",
        "/v1/offers",
        expected=201,
        headers=context["headers"],
        json={
            "application_id": context["application"]["id"],
            "salary_amount": 3_500_000,
            "currency": "CNY",
        },
    ).json()

    _request(
        client,
        "POST",
        f"/v1/offers/{offer['id']}/status",
        expected=409,
        headers=context["headers"],
        json={"status": "accepted", "version": offer["version"]},
    )
    original_version = offer["version"]
    sent = _request(
        client,
        "POST",
        f"/v1/offers/{offer['id']}/status",
        expected=200,
        headers=context["headers"],
        json={"status": "sent", "version": original_version},
    ).json()
    _request(
        client,
        "POST",
        f"/v1/offers/{offer['id']}/status",
        expected=409,
        headers=context["headers"],
        json={"status": "accepted", "version": original_version},
    )
    accepted = _request(
        client,
        "POST",
        f"/v1/offers/{offer['id']}/status",
        expected=200,
        headers=context["headers"],
        json={"status": "accepted", "version": sent["version"]},
    ).json()

    applications = _request(
        client, "GET", "/v1/applications", expected=200, headers=context["headers"]
    ).json()
    application = next(item for item in applications if item["id"] == context["application"]["id"])
    application_audits = _request(
        client,
        "GET",
        f"/v1/applications/{application['id']}/audit",
        expected=200,
        headers=context["headers"],
    ).json()
    offer_audits = _request(
        client,
        "GET",
        f"/v1/offers/{offer['id']}/audit",
        expected=200,
        headers=context["headers"],
    ).json()

    assert accepted["status"] == "accepted"
    assert application["stage"] == "hired"
    assert application_audits[-1]["to_stage"] == "hired"
    assert [item["to_status"] for item in offer_audits] == ["draft", "sent", "accepted"]


def test_cross_tenant_cannot_observe_or_mutate_application(client: httpx.Client) -> None:
    tenant_a = _new_application(client, "tenant-a")
    tenant_b_headers = _bootstrap(client, "tenant-b")
    application = tenant_a["application"]

    visible = _request(
        client, "GET", "/v1/applications", expected=200, headers=tenant_b_headers
    ).json()
    _request(
        client,
        "POST",
        f"/v1/applications/{application['id']}/stage",
        expected=404,
        headers=tenant_b_headers,
        json={"stage": "screening", "version": application["version"]},
    )

    assert application["id"] not in {item["id"] for item in visible}


def test_directory_filters_and_notification_read_state(client: httpx.Client) -> None:
    context = _new_application(client, "filters")
    headers = context["headers"]

    jobs = _request(
        client, "GET", "/v1/jobs?q=Backend%20Engineer&status=open", expected=200, headers=headers
    ).json()
    candidates = _request(
        client, "GET", "/v1/candidates?q=API%20Candidate", expected=200, headers=headers
    ).json()
    applications = _request(
        client, "GET", "/v1/applications?stage=applied&q=API", expected=200, headers=headers
    ).json()

    assert context["job"]["id"] in {item["id"] for item in jobs}
    assert context["candidate"]["id"] in {item["id"] for item in candidates}
    assert context["application"]["id"] in {item["id"] for item in applications}
    assert _request(
        client, "GET", "/v1/applications?stage=hired", expected=200, headers=headers
    ).json() == []

    notifications: list[dict] = []
    for _ in range(20):
        notifications = _request(
            client, "GET", "/v1/notifications", expected=200, headers=headers
        ).json()
        if notifications:
            break
        time.sleep(0.25)
    assert notifications, "expected the Outbox notification consumer to create a notification"

    unread = next(item for item in notifications if item["read_at"] is None)
    marked = _request(
        client,
        "PATCH",
        f"/v1/notifications/{unread['id']}/read",
        expected=200,
        headers=headers,
    ).json()
    assert marked["read_at"] is not None

    result = _request(
        client, "POST", "/v1/notifications/read-all", expected=200, headers=headers
    ).json()
    assert result["updated"] >= 0
