"""Outbound notification providers and presentation templates."""

from __future__ import annotations

import asyncio
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Protocol

EMAIL_TEMPLATE_VERSION = "recruiting-notification.v1"


class ProviderDeliveryError(RuntimeError):
    """Normalized provider failure with durable retry semantics."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        category: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.category = category
        self.status_code = status_code


@dataclass(frozen=True)
class ProviderMessage:
    delivery_id: str
    recipient: str
    subject: str
    text_body: str
    html_body: str | None = None
    template_version: str = EMAIL_TEMPLATE_VERSION


@dataclass(frozen=True)
class ProviderResult:
    message_id: str


class NotificationProvider(Protocol):
    name: str

    async def send(self, message: ProviderMessage) -> ProviderResult: ...


def render_email_template(title: str, content: str) -> tuple[str, str]:
    """Return a readable text body and a deliberately small, portable HTML template."""
    text = f"{title}\n\n{content}\n\n— HireFlow 招聘团队"
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;'
        'padding:28px;color:#172033">'
        '<div style="font-size:13px;color:#52637d">HireFlow · 招聘进展通知</div>'
        f'<h1 style="font-size:24px;margin:14px 0">{escape(title)}</h1>'
        f'<p style="line-height:1.7;color:#43516a">{escape(content)}</p>'
        '<hr style="border:0;border-top:1px solid #e6eaf0;margin:28px 0">'
        '<p style="font-size:12px;color:#8290a6">此邮件由 HireFlow 自动发送。</p></div>'
    )
    return text, html


class SMTPNotificationProvider:
    name = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        timeout_seconds: float = 5.0,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.timeout_seconds = timeout_seconds
        self.username = username
        self.password = password
        self.starttls = starttls

    async def send(self, message: ProviderMessage) -> ProviderResult:
        return await asyncio.wait_for(
            asyncio.to_thread(self._send_sync, message), timeout=self.timeout_seconds
        )

    def _send_sync(self, message: ProviderMessage) -> ProviderResult:
        email = EmailMessage()
        email["From"] = self.sender
        email["To"] = message.recipient
        email["Subject"] = message.subject
        email["Message-ID"] = f"<{message.delivery_id}@hireflow.local>"
        email["X-HireFlow-Template-Version"] = message.template_version
        email.set_content(message.text_body)
        if message.html_body:
            email.add_alternative(message.html_body, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as client:
            if self.starttls:
                client.starttls()
            if self.username:
                client.login(self.username, self.password or "")
            rejected = client.send_message(email)
        if rejected:
            status_code = next(iter(rejected.values()))[0]
            retryable = 400 <= status_code < 500
            category = "rate_limited" if status_code in {421, 450, 451, 452} else (
                "temporary" if retryable else "permanent"
            )
            raise ProviderDeliveryError(
                f"SMTP rejected recipients: {', '.join(rejected)}",
                retryable=retryable,
                category=category,
                status_code=status_code,
            )
        return ProviderResult(message_id=email["Message-ID"])


def provider_from_environment() -> NotificationProvider:
    host = os.getenv("HIREFLOW_SMTP_HOST")
    if not host:
        raise RuntimeError("HIREFLOW_SMTP_HOST must be configured")
    return SMTPNotificationProvider(
        host=host,
        port=int(os.getenv("HIREFLOW_SMTP_PORT", "1025")),
        sender=os.getenv("HIREFLOW_SMTP_SENDER", "HireFlow <no-reply@hireflow.local>"),
        timeout_seconds=float(os.getenv("HIREFLOW_SMTP_TIMEOUT_SECONDS", "5")),
        username=os.getenv("HIREFLOW_SMTP_USERNAME") or None,
        password=os.getenv("HIREFLOW_SMTP_PASSWORD") or None,
        starttls=os.getenv("HIREFLOW_SMTP_STARTTLS", "false").lower() == "true",
    )
