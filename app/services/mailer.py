# app/services/mailer.py
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from typing import Iterable, Optional


@dataclass
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"  # ex: application/pdf


def send_smtp_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    use_tls: bool,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    body_text: str = "",
    body_html: Optional[str] = None,
    attachments: Optional[Iterable[EmailAttachment]] = None,
):
    """
    Envia e-mail SMTP com:
      - texto (text/plain)
      - opcional HTML (text/html)
      - anexos (ex: PDF)
    """

    # Raiz: mixed (pra anexos). Dentro, alternative (plain + html).
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text or "", "plain", "utf-8"))

    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))

    msg.attach(alt)

    # anexos
    for att in (attachments or []):
        part = MIMEBase(*att.content_type.split("/", 1)) if "/" in att.content_type else MIMEBase("application", "octet-stream")
        part.set_payload(att.content)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{att.filename}"')
        part.add_header("Content-Type", att.content_type)
        msg.attach(part)

    server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
    try:
        if use_tls:
            server.starttls()

        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)

        server.sendmail(from_email, [to_email], msg.as_string())
    finally:
        server.quit()