"""Outbound notifications — email the farmer their policy document at binding.

Kept behind SMTP config (AEZ_SMTP_*): with no mail server configured, sending is
a logged no-op, so the platform runs the same locally and in tests. The send is
dispatched as a Celery task from the bind endpoint, so binding stays fast.

Privacy: the policy document is the master schedule, which for a partner sale
lists every farmer under it. To avoid mailing one farmer another's details, the
auto-email is sent only for individual sales (a single insured). Partner-sale
schedules are handled through the workbench.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import settings


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_policy_documents(policy_id: str) -> dict:
    """Email the policy PDF to the insured. Returns a small summary (never raises
    for an unconfigured mailer or a missing email — those are expected no-ops)."""
    from app.policies import get_policy, policy_document_pdf

    master = get_policy(policy_id)
    if not master:
        return {"policy_id": policy_id, "sent": 0, "reason": "no such policy"}
    if master["sale_type"] != "individual":
        return {"policy_id": policy_id, "sent": 0, "reason": "partner sale — not auto-emailed"}

    to = ""
    if master["schedule"]:
        to = (master["schedule"][0]["farmer"].get("email") or "").strip()
    if not to:
        return {"policy_id": policy_id, "sent": 0, "reason": "no email on file"}
    if not smtp_configured():
        return {"policy_id": policy_id, "sent": 0, "reason": "email not configured"}

    pdf = policy_document_pdf(policy_id)
    if not pdf:
        return {"policy_id": policy_id, "sent": 0, "reason": "no document"}

    msg = EmailMessage()
    msg["Subject"] = f"Your policy {policy_id}"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(
        f"Thank you for taking out weather index insurance.\n\n"
        f"Your policy {policy_id} is attached as a PDF — please keep it as your "
        f"record of the cover you bought. It shows the season covered, the stages, "
        f"and how any payout is worked out.\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf",
                       filename=f"{policy_id}.pdf")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)

    return {"policy_id": policy_id, "sent": 1, "to": to}
