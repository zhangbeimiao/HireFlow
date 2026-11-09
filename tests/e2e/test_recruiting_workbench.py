"""Browser-level proof that the recruiting loop is operable without calling APIs directly."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

playwright = pytest.importorskip("playwright.sync_api")
Page = playwright.Page
expect = playwright.expect

BASE_URL = os.getenv("HIREFLOW_E2E_BASE_URL")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not BASE_URL, reason="set HIREFLOW_E2E_BASE_URL to run browser tests"),
]


@pytest.fixture
def page() -> Page:
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        value = context.new_page()
        value.set_default_timeout(7_000)
        yield value
        context.close()
        browser.close()


def _open_operation(page: Page, name: str) -> None:
    page.locator(f'[data-operation="{name}"]').first.click()
    expect(page.locator("#operation-dialog")).to_be_visible()


def _advance(page: Page, target: str) -> None:
    page.locator("[data-advance]").first.click()
    page.locator("#stage-target").select_option(target)
    page.locator("#stage-submit").click()
    expect(page.locator("#operation-dialog")).not_to_be_visible()


def test_frontend_completes_recruiting_loop(page: Page) -> None:
    suffix = uuid4().hex[:10]
    candidate_name = f"端到端候选人 {suffix}"
    page.goto(BASE_URL or "http://127.0.0.1:8000", wait_until="domcontentloaded")

    page.locator("#open-auth").click()
    page.locator('[data-tab="bootstrap"]').click()
    page.locator("#tenant-name").fill(f"Playwright 招聘团队 {suffix}")
    page.locator("#owner-name").fill("自动化招聘负责人")
    page.locator("#owner-email").fill(f"owner.{suffix}@hireflow.demo")
    page.locator("#owner-password").fill("PlaywrightPassword!2026")
    page.locator("#bootstrap-submit").click()
    expect(page.locator("#open-auth")).to_contain_text("已连接")

    _open_operation(page, "job")
    page.locator("#job-title").fill(f"后端工程师 {suffix}")
    page.locator("#job-submit").click()
    expect(page.locator("#job-list")).to_contain_text(f"后端工程师 {suffix}")

    _open_operation(page, "candidate")
    page.locator("#candidate-name").fill(candidate_name)
    page.locator("#candidate-email").fill(f"candidate.{suffix}@hireflow.demo")
    page.locator("#candidate-submit").click()
    expect(page.locator("#candidate-list")).to_contain_text(candidate_name)

    _open_operation(page, "application")
    page.locator("#application-submit").click()
    expect(page.locator("#application-list")).to_contain_text(candidate_name)

    starts_at = datetime.now().replace(second=0, microsecond=0) + timedelta(days=2, hours=1)
    ends_at = starts_at + timedelta(hours=1)
    _open_operation(page, "schedule")
    page.locator("#schedule-application").select_option(index=1)
    page.locator("#schedule-start").fill(starts_at.strftime("%Y-%m-%dT%H:%M"))
    page.locator("#schedule-end").fill(ends_at.strftime("%Y-%m-%dT%H:%M"))
    page.locator("#schedule-submit").click()
    expect(page.locator("#operation-dialog")).not_to_be_visible()
    expect(page.locator("#interview-list")).to_contain_text(candidate_name)

    _advance(page, "screening")
    _advance(page, "interview")

    _open_operation(page, "scorecard")
    page.locator("#scorecard-summary").fill("证据充分，建议进入 Offer 阶段。")
    page.locator("#scorecard-submit-final").check()
    page.locator("#scorecard-submit").click()
    expect(page.locator("#operation-dialog")).not_to_be_visible()

    _advance(page, "offer")
    _open_operation(page, "offer")
    page.locator("#offer-salary").fill("30000")
    page.locator("#offer-submit").click()
    expect(page.locator("#offer-list")).to_contain_text(candidate_name)

    page.locator("[data-offer-action]").first.click()
    expect(page.locator("#operation-dialog")).to_be_visible()
    page.locator("#offer-status-target").select_option("sent")
    page.locator("#offer-status-submit").click()
    page.locator("[data-offer-action]").first.click()
    expect(page.locator("#operation-dialog")).to_be_visible()
    page.locator("#offer-status-target").select_option("accepted")
    page.locator("#offer-status-submit").click()

    expect(page.locator("#application-list")).to_contain_text("已录用")
    page.locator("[data-application-detail]").first.click()
    expect(page.locator("#ats-detail")).to_be_visible()
    expect(page.locator("#audit-timeline")).to_contain_text("已录用")
