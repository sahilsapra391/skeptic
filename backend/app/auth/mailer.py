"""Transactional email (launch L1b): exactly one job — deliver the
verification link. Sender is env-picked so the owner can swap providers
without code: RESEND_API_KEY → Resend, SKEPTIC_SMTP_HOST → any SMTP,
neither → the link is logged server-side (dev / pre-sender deploys; the
signup flow still works, verification just waits on the owner step).
Email addresses are the only PII touched here; nothing else is logged.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

log = logging.getLogger("auth.mailer")

FROM_DEFAULT = "Skeptic <verify@skeptic.fyi>"


def app_url() -> str:
    return os.environ.get("SKEPTIC_APP_URL", "https://skeptic.fyi").rstrip("/")


def send_verification(email: str, token: str) -> bool:
    """True when handed to a real sender; False when only logged (no
    sender configured) or delivery failed — callers surface that honestly
    instead of pretending mail is on its way."""
    link = f"{app_url()}/verify?token={token}"
    subject = "Verify your email — Skeptic"
    body = (
        "Confirm this address to finish setting up your Skeptic account:\n\n"
        f"{link}\n\n"
        "The link works once and expires in 3 days. If you didn't create "
        "this account, ignore this email — nothing happens without the link."
    )

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        try:
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": os.environ.get("SKEPTIC_MAIL_FROM", FROM_DEFAULT),
                    "to": [email],
                    "subject": subject,
                    "text": body,
                },
                timeout=10,
            )
            if r.status_code in (200, 201):
                return True
            log.error("resend delivery failed: HTTP %s", r.status_code)
            return False
        except requests.RequestException:
            log.exception("resend delivery failed")
            return False

    host = os.environ.get("SKEPTIC_SMTP_HOST")
    if host:
        try:
            msg = EmailMessage()
            msg["From"] = os.environ.get("SKEPTIC_MAIL_FROM", FROM_DEFAULT)
            msg["To"] = email
            msg["Subject"] = subject
            msg.set_content(body)
            port = int(os.environ.get("SKEPTIC_SMTP_PORT", "587"))
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.starttls()
                user = os.environ.get("SKEPTIC_SMTP_USER")
                if user:
                    smtp.login(user, os.environ.get("SKEPTIC_SMTP_PASSWORD", ""))
                smtp.send_message(msg)
            return True
        except Exception:
            log.exception("smtp delivery failed")
            return False

    # no sender configured — the owner-visible dev path; the LINK is logged
    # (it's single-use and short-lived), never any credential
    log.warning("no mail sender configured — verification link for %s: %s", email, link)
    return False
