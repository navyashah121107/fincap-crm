import base64
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

import requests


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(text, ctx):
    """Replace {name}, {person}, {policy_number} ... ; unknown placeholders stay as-is."""
    try:
        return (text or "").format_map(_Safe(ctx))
    except (ValueError, IndexError, KeyError):
        return text or ""


def normalize_mobile(mobile):
    digits = re.sub(r"\D", "", mobile or "")
    digits = digits.lstrip("0")
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def wa_link(mobile, body):
    num = normalize_mobile(mobile)
    if not num:
        return None
    return f"https://wa.me/{num}?text={quote(body)}"


def _smtp_settings():
    user = (os.getenv("SMTP_USER") or "").strip()
    pw = (os.getenv("SMTP_PASSWORD") or "").replace(" ", "")  # Google shows the App Password with spaces
    return user, pw


def email_api_provider():
    p = (os.getenv("EMAIL_API") or "").strip().lower()
    if p == "gmail":
        need = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
        return "gmail" if all((os.getenv(k) or "").strip() for k in need) else ""
    return p if p in ("resend", "brevo") and (os.getenv("EMAIL_API_KEY") or "").strip() else ""


def _gmail_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", timeout=20, data={
        "client_id": os.getenv("GMAIL_CLIENT_ID").strip(), "client_secret": os.getenv("GMAIL_CLIENT_SECRET").strip(),
        "refresh_token": os.getenv("GMAIL_REFRESH_TOKEN").strip(), "grant_type": "refresh_token"})
    if not r.ok:
        raise RuntimeError(f"Google refused the saved sign-in ({r.status_code}): {r.text[:200]} "
                           "-> run get_gmail_token.py again")
    return r.json()["access_token"]


def _send_email_gmail(to, subject, body, from_name, reply_to):
    """Sends through the Gmail web API (HTTPS), so it works on hosts that block SMTP ports."""
    sender = (os.getenv("GMAIL_SENDER") or os.getenv("MAIL_FROM_ADDRESS") or "").strip()
    company = os.getenv("MAIL_FROM_NAME", "")
    display = f"{from_name} - {company}" if from_name and company else (from_name or company)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject or company or "Message"
    if sender:
        msg["From"] = formataddr((display, sender))
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        token = _gmail_access_token()
        r = requests.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                          headers={"Authorization": f"Bearer {token}"}, json={"raw": raw}, timeout=25)
        if r.ok:
            return True, ""
        return False, f"Gmail rejected the email ({r.status_code}): {r.text[:250]}"
    except Exception as exc:  # noqa
        return False, str(exc)


def email_configured():
    if email_api_provider():
        return True
    user, pw = _smtp_settings()
    placeholder = user.startswith("youraddress") or pw.startswith("your-16")
    return bool(user and pw and not placeholder)


def whatsapp_api_configured():
    return bool(os.getenv("WHATSAPP_TOKEN"))


def _send_email_api(provider, to, subject, body, from_email, from_name, reply_to):
    sender = (from_email or os.getenv("MAIL_FROM_ADDRESS") or "").strip()
    if not sender:
        return False, "No sender address: set the company email ID on the Account / Employees page."
    company = os.getenv("MAIL_FROM_NAME", "")
    display = f"{from_name} - {company}" if from_name and company else (from_name or company)
    key = os.getenv("EMAIL_API_KEY").strip()
    try:
        if provider == "resend":
            r = requests.post("https://api.resend.com/emails",
                              headers={"Authorization": f"Bearer {key}"},
                              json={"from": formataddr((display, sender)), "to": [to], "subject": subject or company,
                                    "text": body, "reply_to": reply_to or sender}, timeout=25)
        else:  # brevo
            r = requests.post("https://api.brevo.com/v3/smtp/email",
                              headers={"api-key": key, "accept": "application/json"},
                              json={"sender": {"name": display or sender, "email": sender},
                                    "to": [{"email": to}], "subject": subject or company, "textContent": body,
                                    "replyTo": {"email": reply_to or sender}}, timeout=25)
        if r.ok:
            return True, ""
        return False, f"{provider} rejected the email ({r.status_code}): {r.text[:250]}"
    except Exception as exc:  # noqa
        return False, str(exc)


def send_email(to, subject, body, reply_to=None, from_name=None, from_email=None,
               login_user=None, login_password=None):
    if not to:
        return False, "Client has no email address"
    provider = email_api_provider()
    if provider == "gmail":
        return _send_email_gmail(to, subject, body, from_name, reply_to)
    if provider:  # HTTPS email service: works on hosts that block SMTP ports (e.g. Render free plan)
        return _send_email_api(provider, to, subject, body, from_email, from_name, reply_to)
    own_login = bool(login_user and login_password)
    if not own_login and not email_configured():
        return False, ("SMTP_USER / SMTP_PASSWORD in .env are missing or still the example values. "
                       "Put your Gmail address and a 16-character Google App Password there, then restart the app.")
    user, pw = _smtp_settings()
    if own_login:  # the employee's own company mailbox logs in and sends
        user, pw = login_user.strip(), login_password
    msg = EmailMessage()
    msg["Subject"] = subject or os.getenv("COMPANY_NAME", "Message")
    company = os.getenv("MAIL_FROM_NAME", "")
    display = f"{from_name} - {company}" if from_name and company else (from_name or company)
    # Mail goes out from the handling employee's COMPANY email id. The mail server must be allowed to send
    # for that address (see README); replies always come back to it.
    sender_addr = (from_email or user).strip()
    msg["From"] = formataddr((display, sender_addr))
    msg["Reply-To"] = (reply_to or sender_addr)
    msg["To"] = to
    msg.set_content(body)
    host = (os.getenv("SMTP_HOST") or "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "587") or 587)
    attempts = [port] + [p for p in (587, 465) if p != port]  # try the other common port as a fallback
    ctx = ssl.create_default_context()
    last = ""
    for p in attempts:
        try:
            if p == 465:
                with smtplib.SMTP_SSL(host, p, timeout=25, context=ctx) as s:
                    s.login(user, pw)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(host, p, timeout=25) as s:
                    s.ehlo()
                    s.starttls(context=ctx)
                    s.ehlo()
                    s.login(user, pw)
                    s.send_message(msg)
            return True, ""
        except smtplib.SMTPAuthenticationError:
            return False, (f"The mail server rejected the login for {user}. Check the mailbox password "
                           "(Gmail / Google Workspace need a 16-character App Password with 2-Step Verification ON) "
                           "and that SMTP sending is enabled for that mailbox.")
        except smtplib.SMTPRecipientsRefused:
            return False, "The client's email address was refused. Check it for typing mistakes."
        except Exception as exc:  # noqa
            last = f"port {p}: {exc}"
    return False, ("Could not reach the mail server (" + last + "). Check your internet, and that an antivirus, "
                   "firewall or office network is not blocking ports 587/465.")


WA_PARAMS = {  # order of the {{1}}, {{2}}... variables of each approved WhatsApp template
    "birthday": ["name", "wish_for", "employee_name", "employee_mobile"],
    "anniversary": ["name", "couple", "employee_name", "employee_mobile"],
    "renewal": ["name", "policy_type", "policy_number", "expiry_date", "employee_name", "employee_mobile"],
}


def wa_template(category, ctx):
    """Approved-template details for a message type, or None when WA_TEMPLATE_<TYPE> is not set in .env."""
    name = (os.getenv(f"WA_TEMPLATE_{(category or '').upper()}") or "").strip()
    if not name or category not in WA_PARAMS:
        return None
    clean = lambda v: " ".join(str(v or "-").split()) or "-"  # WhatsApp rejects newlines/tabs in variables
    return dict(name=name, lang=(os.getenv("WA_TEMPLATE_LANG") or "en").strip(),
                params=[clean(ctx.get(k)) for k in WA_PARAMS[category]])


def send_whatsapp_api(mobile, body, phone_id=None, template=None):
    """Sends straight from the employee's company number through the WhatsApp Business Cloud API.
    Business-started chats must use an approved template; plain text only works inside 24h after the
    client last wrote to that number."""
    num = normalize_mobile(mobile)
    if not num:
        return False, "Client has no mobile number"
    if not whatsapp_api_configured():
        return False, "WhatsApp API is not configured (WHATSAPP_TOKEN missing)"
    pid = (phone_id or "").strip()
    if not pid:
        return False, ("No company WhatsApp number (phone-number ID) is linked to the employee who handles this "
                       "client. Add it under Employees.")
    if template:
        payload = {"messaging_product": "whatsapp", "to": num, "type": "template",
                   "template": {"name": template["name"], "language": {"code": template["lang"]}}}
        if template["params"]:
            payload["template"]["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in template["params"]]}]
    else:
        payload = {"messaging_product": "whatsapp", "to": num, "type": "text", "text": {"body": body}}
    try:
        r = requests.post(f"https://graph.facebook.com/v20.0/{pid}/messages",
                          headers={"Authorization": f'Bearer {os.getenv("WHATSAPP_TOKEN")}'},
                          json=payload, timeout=20)
        if r.ok:
            return True, ""
        try:
            err = r.json().get("error", {})
            detail = err.get("error_data", {}).get("details") or err.get("message") or r.text
        except Exception:  # noqa
            detail = r.text
        hint = "" if template else " (plain text only reaches people who messaged this number in the last 24h; set WA_TEMPLATE_* in .env)"
        return False, str(detail)[:300] + hint
    except Exception as exc:  # noqa
        return False, str(exc)
