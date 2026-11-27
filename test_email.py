"""Checks that email sending works.

  python test_email.py                      -> sends a test mail to SMTP_USER
  python test_email.py someone@gmail.com    -> sends it to that address (use this with EMAIL_API=resend/brevo)
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()
import messaging

to = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMTP_USER")
print("Mode           :", messaging.email_api_provider() or "SMTP")
print("SMTP_USER      :", os.getenv("SMTP_USER"))
print("SMTP_HOST/PORT :", os.getenv("SMTP_HOST"), os.getenv("SMTP_PORT"))
print("Configured     :", messaging.email_configured())
print("Sending to     :", to)
ok, err = messaging.send_email(to, "CRM test email", "If you can read this, email sending works.")
print("RESULT         :", "SENT - check the inbox (and spam)" if ok else "FAILED -> " + err)
