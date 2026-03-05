# app/services/mailer.py
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional, Sequence


@dataclass
class EmailAttachment:
    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


def _split_content_type(content_type: str) -> tuple[str, str]:
    ct = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if "/" in ct:
        maintype, subtype = ct.split("/", 1)
        return maintype or "application", subtype or "octet-stream"
    return "application", "octet-stream"


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
    body_text: str,
    body_html: Optional[str] = None,
    attachments: Optional[Sequence[EmailAttachment]] = None,
) -> None:
    """
    Envia e-mail com:
      - texto (plain)
      - html opcional
      - anexos opcionais (PDF, etc.)
    """
    attachments = list(attachments or [])

    # Se tiver anexo, precisa ser mixed; senão alternative basta
    msg = MIMEMultipart("mixed") if attachments else MIMEMultipart("alternative")
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    # Parte alternative (sempre)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text or "", "plain", "utf-8"))
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alt)

    # Anexos
    for att in attachments:
        maintype, subtype = _split_content_type(att.content_type)

        part = MIMEBase(maintype, subtype)
        part.set_payload(att.content or b"")
        encoders.encode_base64(part)

        filename = (att.filename or "attachment").strip() or "attachment"
        part.add_header("Content-Disposition", "attachment", filename=filename)
        # opcional: reforça content-type
        part.add_header("Content-Type", f"{maintype}/{subtype}", name=filename)

        msg.attach(part)

    server = smtplib.SMTP(smtp_host, int(smtp_port), timeout=30)
    try:
        if use_tls:
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.sendmail(from_email, [to_email], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass