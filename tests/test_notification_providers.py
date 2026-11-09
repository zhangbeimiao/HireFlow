import asyncio
from email.message import EmailMessage
from typing import ClassVar, Self

import pytest

from hireflow.email_dispatcher import retry_delay
from hireflow.notification_providers import (
    ProviderMessage,
    SMTPNotificationProvider,
    render_email_template,
)


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []
    rejected: ClassVar[dict[str, tuple[int, bytes]]] = {}

    def __init__(self, host: str, port: int, *, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.message: EmailMessage | None = None
        self.logged_in: tuple[str, str] | None = None
        self.tls_started = False
        self.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def starttls(self) -> None:
        self.tls_started = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        self.message = message
        return self.rejected


def provider(*, timeout_seconds: float = 1.0) -> SMTPNotificationProvider:
    return SMTPNotificationProvider(
        host="smtp.test",
        port=2525,
        sender="HireFlow <no-reply@hireflow.test>",
        timeout_seconds=timeout_seconds,
        username="service",
        password="secret",
        starttls=True,
    )


def message() -> ProviderMessage:
    return ProviderMessage(
        delivery_id="11111111-1111-1111-1111-111111111111",
        recipient="candidate@example.com",
        subject="面试已安排",
        text_body="面试时间：明天 10:00",
        html_body="<strong>明天 10:00</strong>",
    )


def test_template_escapes_user_content() -> None:
    text_body, html_body = render_email_template("<script>", "A & B")

    assert "<script>" in text_body
    assert "&lt;script&gt;" in html_body
    assert "A &amp; B" in html_body


@pytest.mark.asyncio
async def test_smtp_provider_sends_multipart_message_with_stable_id(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    FakeSMTP.rejected = {}
    monkeypatch.setattr("hireflow.notification_providers.smtplib.SMTP", FakeSMTP)

    result = await provider().send(message())

    client = FakeSMTP.instances[0]
    assert client.tls_started is True
    assert client.logged_in == ("service", "secret")
    assert client.message is not None
    assert client.message["Message-ID"] == result.message_id
    assert result.message_id == "<11111111-1111-1111-1111-111111111111@hireflow.local>"
    assert client.message.is_multipart()


@pytest.mark.asyncio
async def test_smtp_provider_surfaces_rejected_recipient(monkeypatch) -> None:
    FakeSMTP.instances.clear()
    FakeSMTP.rejected = {"candidate@example.com": (550, b"rejected")}
    monkeypatch.setattr("hireflow.notification_providers.smtplib.SMTP", FakeSMTP)

    with pytest.raises(RuntimeError, match="rejected recipients"):
        await provider().send(message())


@pytest.mark.asyncio
async def test_smtp_provider_enforces_timeout(monkeypatch) -> None:
    async def never_finishes(*_: object) -> None:
        await asyncio.sleep(1)

    smtp_provider = provider(timeout_seconds=0.01)
    monkeypatch.setattr("hireflow.notification_providers.asyncio.to_thread", never_finishes)

    with pytest.raises(TimeoutError):
        await smtp_provider.send(message())


def test_retry_delay_is_exponential_and_capped() -> None:
    assert retry_delay(1).total_seconds() == 1
    assert retry_delay(4).total_seconds() == 8
    assert retry_delay(20).total_seconds() == 300
