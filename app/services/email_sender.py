import smtplib
from email.message import EmailMessage

from app.models.company import Company


class EmailSenderError(Exception):
    pass


def send_email_smtp(
    company: Company,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    # valida config mínima
    if not company.smtp_host or not company.smtp_port or not company.smtp_user or not company.smtp_password:
        raise EmailSenderError("SMTP não configurado: defina smtp_host, smtp_port, smtp_user e smtp_password na empresa.")

    from_email = company.from_email or company.smtp_user
    from_name = company.from_name or company.nome or "Cobrax"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email

    # Por enquanto: texto puro (mais simples)
    msg.set_content(body)

    try:
        server = smtplib.SMTP(company.smtp_host, company.smtp_port, timeout=20)

        # STARTTLS (mais comum em 587)
        if company.smtp_use_tls:
            server.starttls()

        server.login(company.smtp_user, company.smtp_password)
        server.send_message(msg)
        server.quit()

    except Exception as e:
        raise EmailSenderError(f"Falha ao enviar email via SMTP: {e}")
