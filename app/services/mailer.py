# app/services/mailer.py
import smtplib
from typing import Iterable, Optional, Dict, Any, List

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


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
    attachments: Optional[List[Dict[str, Any]]] = None,
):
    """
    attachments: lista de dicts:
      {
        "filename": "boleto.pdf",
        "content": b"...",
        "mime": "application/pdf"
      }
    """

    msg = MIMEMultipart()
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    # Se tiver HTML, manda multipart/alternative (texto + html)
    if body_html:
        alt = MIMEMultipart("alternative")

        # sempre manda um texto fallback
        alt.attach(MIMEText(body_text or "(visualize em um cliente de e-mail HTML)", "plain", "utf-8"))
        alt.attach(MIMEText(body_html, "html", "utf-8"))

        msg.attach(alt)
    else:
        msg.attach(MIMEText(body_text or "", "plain", "utf-8"))

    # anexos
    for a in (attachments or []):
        content = a.get("content") or b""
        filename = a.get("filename") or "anexo.bin"
        mime = a.get("mime") or "application/octet-stream"

        part = MIMEApplication(content, _subtype=mime.split("/")[-1])
        part.add_header("Content-Disposition", "attachment", filename=filename)
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