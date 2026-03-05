# app/services/mailer.py
import smtplib
from typing import Iterable, Optional, List, Dict, Any

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
    attachments: Optional[Iterable[Dict[str, Any]]] = None,
):
    """
    Envia e-mail via SMTP com suporte a:
      - texto puro (body_text)
      - HTML (body_html)
      - anexos (attachments)

    attachments: lista de dicts:
      {"filename": "boleto.pdf", "content": b"...", "mime_subtype": "pdf"}
      mime_subtype padrão: "octet-stream"
    """

    # container "mixed" para anexos
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject

    # parte alternativa (texto + html)
    alt = MIMEMultipart("alternative")

    # texto fallback
    alt.attach(MIMEText(body_text or "", "plain", "utf-8"))

    # html (se existir)
    if body_html:
        alt.attach(MIMEText(body_html, "html", "utf-8"))

    msg.attach(alt)

    # anexos
    if attachments:
        for a in attachments:
            filename = str(a.get("filename") or "anexo")
            content = a.get("content") or b""
            subtype = str(a.get("mime_subtype") or "octet-stream")

            part = MIMEApplication(content, _subtype=subtype)
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