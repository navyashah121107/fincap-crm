"""One-time helper: lets the CRM send email through YOUR Gmail account over the web (no App Password, no SMTP).

Usage:  python get_gmail_token.py CLIENT_ID CLIENT_SECRET
Sign in with the Gmail account that should send all the mail, press Allow, then copy the lines it prints into .env.
"""
import http.server
import sys
import urllib.parse
import webbrowser

import requests

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(1)
CLIENT_ID, CLIENT_SECRET = sys.argv[1].strip(), sys.argv[2].strip()
PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        result["code"] = (q.get("code") or [None])[0]
        result["error"] = (q.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h2>Done. You can close this tab and go back to the terminal.</h2>")

    def log_message(self, *a):
        pass


url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code", "scope": SCOPE,
    "access_type": "offline", "prompt": "consent"})
print("Opening your browser. If it does not open, paste this address into it:\n", url, "\n")
webbrowser.open(url)
http.server.HTTPServer(("localhost", PORT), Handler).handle_request()
if not result.get("code"):
    print("No code received:", result.get("error"))
    sys.exit(1)
r = requests.post("https://oauth2.googleapis.com/token", data={
    "code": result["code"], "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    "redirect_uri": REDIRECT, "grant_type": "authorization_code"}, timeout=20)
data = r.json()
if "refresh_token" not in data:
    print("Google did not return a refresh token:", data)
    sys.exit(1)
print("\nAdd these lines to your .env (and to the server's environment settings):\n")
print("EMAIL_API=gmail")
print("SHARED_MAIL=1")
print(f"GMAIL_CLIENT_ID={CLIENT_ID}")
print(f"GMAIL_CLIENT_SECRET={CLIENT_SECRET}")
print(f"GMAIL_REFRESH_TOKEN={data['refresh_token']}")
print("GMAIL_SENDER=the-gmail-address-you-just-signed-in-with@gmail.com")
print("MAIL_FROM_NAME=Shashwat Fincap")
