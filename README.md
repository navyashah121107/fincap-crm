# Shashwat Fincap CRM

Flask + PostgreSQL client desk: owner (full control) and employees (view only, owner picks what they can see).
Clients, family dates, SIP, insurance (5 types), life and fire insurance, birthday/anniversary/renewal messages,
dashboard counts and Excel export.

## 1. Run it on your computer (optional but recommended first)
```
python -m venv venv
venv\Scripts\activate          # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # Mac/Linux: cp .env.example .env   then edit .env
python app.py
```
Open http://127.0.0.1:5000 and sign in with OWNER_USERNAME / OWNER_PASSWORD from `.env`.
With no DATABASE_URL it uses a local file `crm.db` (SQLite) so you can test without any database.

## 2. Create the free online database (PostgreSQL)
1. Go to https://neon.tech and sign up -> Create project (choose the Singapore/Mumbai-nearest region).
2. Copy the **connection string** (starts with `postgresql://`). That is your `DATABASE_URL`.
   (Alternatives: https://supabase.com or Render's own PostgreSQL. Check each one's current free-plan limits.)
Tables are created automatically the first time the app starts.

## 3. Put the code on GitHub
1. Create a **private** repository at https://github.com/new (e.g. `fincap-crm`).
2. In this folder:
```
git init
git add .
git commit -m "First version of CRM"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/fincap-crm.git
git push -u origin main
```
`.env` is in `.gitignore`, so your passwords are never uploaded.

## 4. Deploy on Render
1. https://render.com -> New + -> **Web Service** -> connect the GitHub repo.
2. Runtime Python, Build command `pip install -r requirements.txt`, Start command `gunicorn app:app`
   (`render.yaml` already contains this).
3. Environment -> add: `DATABASE_URL`, `SECRET_KEY` (long random text), `OWNER_USERNAME`, `OWNER_PASSWORD`,
   `CRON_KEY`, `COMPANY_NAME`, `WEBSITE_URL`, plus the email keys below.
4. Deploy. You get `https://fincap-crm.onrender.com` (the "dummy domain"). Sign in and test everything.
5. Free Render services go to sleep when idle, so the first open can take ~30-60 seconds.

## 5. Move to a subdomain (after testing)
1. Render -> your service -> Settings -> Custom Domains -> add `crm.shashwatfincap.com`.
2. At your domain registrar's DNS page add a **CNAME** record: Name `crm`, Value = the `.onrender.com`
   address Render shows. Wait for Render to show the domain as verified (HTTPS is automatic).
3. Link it from your website, e.g. a small "Staff login" link in the footer:
   `<a href="https://crm.shashwatfincap.com">Staff login</a>`
   (The CRM is a separate app on a subdomain, not code pasted inside the website. This is the safe way.)

## 6. Sending messages
* **Email (works fully):** use a Gmail/Workspace address -> Google Account -> Security -> 2-Step Verification on
  -> https://myaccount.google.com/apppasswords -> create an App Password. Put it in `SMTP_USER` / `SMTP_PASSWORD`.
* **WhatsApp, free:** each person has an **Open WhatsApp** button that opens WhatsApp with the message filled in;
  you press Send. (Fully automatic WhatsApp is not possible without the official API.)
* **WhatsApp, automatic:** needs the WhatsApp Business Cloud API (https://developers.facebook.com/docs/whatsapp/cloud-api).
  Put `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_ID` in the environment. Meta only lets a business start a chat with
  **pre-approved message templates**, so plain text may be rejected for cold wishes; the Send history page shows the reason.
  Plan on creating and approving templates in Meta's console, then adapting `send_whatsapp_api` in `messaging.py`.
* **Daily automatic wishes:** create a free job at https://cron-job.org that opens
  `https://YOUR-CRM-URL/cron/daily?key=YOUR_CRON_KEY` once a day (e.g. 9:00 AM). It sends today's birthday and
  anniversary wishes by email (and WhatsApp API if configured), using the first draft of that type, and never
  sends the same wish twice.

## 7. How roles work
* Owner: everything, including registering employees, choosing what each can view, sending messages, exports,
  changing own username/password (Account page).
* Employee: can only **view**. The owner ticks what they may see: clients, policies, messages, export.

## Files
`app.py` routes and logic · `models.py` database tables · `messaging.py` email/WhatsApp · `templates/` pages ·
`static/style.css` design · `render.yaml`, `Procfile` deployment.

## Security notes
Keep the GitHub repo private, use a strong owner password, never commit `.env`, and take periodic backups
(Neon/Supabase dashboards, or Excel exports from the sidebar). Client data is personal data: limit employee access.

## Employees, their clients and their numbers
* Owner: **Employees -> Register employee**. Enter the employee ID (login), password, the employee's own mobile /
  WhatsApp number, optional email, and tick what the employee may do (view / add / edit / delete / send).
* Give clients to an employee: choose "Handled by" when adding or editing a client, or on the **Clients** page tick
  clients and use "Hand the ticked clients to".
* An employee sees only the clients handled by them (their policies, renewals, birthdays, send history and exports too).
* Every message for a client (birthday, anniversary, renewal, query) is sent for the employee who handles that client:
  emails show that employee's name and use their email as Reply-To, and drafts can contain `{employee_name}` and
  `{employee_mobile}` so the client sees who to call. Clients not given to anyone use the owner's number (Account page).
* WhatsApp: the **Open WhatsApp** button uses the WhatsApp that is signed in on that computer or phone, so the handling
  employee should press it from their own login. Fully automatic WhatsApp from each employee's own number needs one
  WhatsApp Business API phone number per employee (fill in "phone-number ID" on the employee form).
* Upgrading an existing database is automatic: the app adds the new columns when it starts.
* After upgrading, edit the drafts on **Message drafts** and add `{employee_name} ({employee_mobile})` to the signature.

## Logo and colours
* Save your logo as `static/logo.png` (or `logo.jpg`, `logo.svg`, `logo.webp`). It replaces the name in the sidebar,
  the login page and the browser tab icon. Without a file the CRM shows the logo from your website
  (`LOGO_URL` in `.env` can point somewhere else). If the logo cannot load, the company name is shown instead.
* The 4 brand colours are at the top of `static/style.css`; change them to match your logo.

## Company email ID and company number on every message
* Each employee is registered with a **company email ID** and a **company mobile / WhatsApp number** (required).
  The owner adds his own on the Account page. Set `COMPANY_EMAIL_DOMAIN=shashwatfincap.com` in `.env` so only
  company addresses are accepted.
* Emails for a client are sent **From the email ID of the employee who handles that client** (the owner's ID if the
  client is not given to anyone), whoever presses Send. Replies go to the same address. If an employee has no
  company email ID, sending is blocked with a clear message instead of using another address.
* **Important:** a mail server only lets you send "From" an address it is allowed to send for. Use one of these:
  1. A mail relay with your domain verified (Brevo, Amazon SES, Resend, etc.: add the SPF/DKIM records they give you
     for shashwatfincap.com; then any `@shashwatfincap.com` address works). Put their SMTP host/user/password in `.env`.
  2. Google Workspace: add each employee address as an alias of the sending account (or "Send mail as"), then
     keep using `smtp.gmail.com`. With a normal free Gmail account, Gmail replaces the From address with the
     login address; the employee name and Reply-To still show.
* WhatsApp: automatic sending always uses the handling employee's own company number (the phone-number ID on the
  employee form); there is no fallback to another number. The **Open WhatsApp** button is shown only to the employee
  who handles that client (and to the owner for clients he handles), and it opens the WhatsApp signed in on that
  device, so that employee must have the company number's WhatsApp there.

## Direct WhatsApp from each employee's company number (no WhatsApp window opens)
This uses Meta's official WhatsApp Business Cloud API. Plain WhatsApp cannot send by itself.
1. https://business.facebook.com -> create a Business account and verify the business.
2. https://developers.facebook.com -> My Apps -> Create app (type Business) -> add the **WhatsApp** product.
3. In WhatsApp -> API Setup: add each employee's company number as a phone number of your WhatsApp Business
   Account and verify it by the SMS/voice code. (A number that is already in the normal WhatsApp app has to be
   handled per Meta's current rules; check Meta's documentation before moving a number.)
4. Copy each number's **Phone number ID** into that employee's form (Employees -> Edit). Owner: Account page.
5. Create a permanent access token (Business settings -> System users -> add one -> give it the WhatsApp app ->
   generate token with whatsapp_business_messaging + whatsapp_business_management). Put it in `.env` as `WHATSAPP_TOKEN`.
6. WhatsApp -> Message templates -> create these 3 templates (category Utility for renewal; Marketing for wishes),
   language English, and wait for approval. Keep the variables exactly in this order:
   * `birthday_wish`: Dear {{1}}, wishing {{2}} a very happy birthday! May the year ahead bring good health, happiness and prosperity. Warm regards, {{3}} ({{4}}), Shashwat Fincap
   * `anniversary_wish`: Dear {{1}}, wishing {{2}} a very happy wedding anniversary! May your bond grow stronger every year. Warm regards, {{3}} ({{4}}), Shashwat Fincap
   * `policy_renewal_reminder`: Dear {{1}}, your {{2}} policy no. {{3}} is due for renewal on {{4}}. Please contact {{5}} on {{6}} to renew on time and keep your cover uninterrupted. Regards, Shashwat Fincap
   Variables: birthday/anniversary = client name, person(s) being wished, employee name, employee number;
   renewal = client name, policy type, policy number, end date, employee name, employee number.
7. Set `WA_TEMPLATE_BIRTHDAY`, `WA_TEMPLATE_ANNIVERSARY`, `WA_TEMPLATE_RENEWAL` and `WA_TEMPLATE_LANG` in `.env`
   to the template names/language. Restart. On the Birthdays and Renewals pages WhatsApp is now ticked by default
   and messages leave directly from the handling employee's number. Check **Send history** for any failure reason.
Meta charges per message and has rules for business messages; check current pricing and policies.

## Email: enabling SMTP (and what to do on Render)
**Render's free plan blocks the SMTP ports (25, 465, 587) since 26 Sept 2025.** Email by SMTP works on your own
computer, but on Render you need either a paid instance or an HTTPS email service:
* HTTPS service (recommended): create an account at https://resend.com or https://www.brevo.com, verify
  shashwatfincap.com (add the DNS records they show), create an API key, then in `.env` / Render Environment set
  `EMAIL_API=resend` (or `brevo`) and `EMAIL_API_KEY=...`. Emails then go From each employee's company email ID.
* SMTP (own computer or a paid Render instance):
  1. Gmail / Google Workspace mailbox: sign in -> Google Account -> Security -> turn on 2-Step Verification ->
     https://myaccount.google.com/apppasswords -> create an App Password (16 characters). Host `smtp.gmail.com`,
     port 587. (IMAP/POP are not needed for sending. In Workspace the admin may have to allow app passwords.)
  2. Other mail hosts (GoDaddy, Zoho, Microsoft 365, cPanel): ask the provider or open its help page for the SMTP
     host and port (usually port 587 with STARTTLS or 465 with SSL), username = the full email address, password =
     the mailbox password. Make sure SMTP / "Authenticated SMTP" is enabled for the mailbox in its admin panel
     (Microsoft 365: Users -> select user -> Mail -> Manage email apps). Put the host/port in `SMTP_HOST` / `SMTP_PORT`.
  3. Either put one shared login in `SMTP_USER` / `SMTP_PASSWORD`, or type each employee's mailbox password on the
     employee form (stored encrypted) so each email is sent by that employee's own mailbox.
  4. Run `python test_email.py` to test.
Renewal reminders by email: Renewals page -> tick policies -> Email -> Send now, or automatically through the
daily job (`/cron/daily`) at the days set in `RENEWAL_REMINDER_DAYS`.

## Which message goes where
* Birthday and anniversary wishes: **WhatsApp only**, straight to the client from the handling employee's company number.
* Renewal reminders: **WhatsApp and email**, from that employee's company number and company email ID.
* Automatic daily sending needs the daily job: create a free job at https://cron-job.org that opens
  `https://YOUR-CRM-URL/cron/daily?key=YOUR_CRON_KEY` every morning (e.g. 9:00 AM India time). It also wakes a sleeping
  free Render service. Without it, use the **Send now** buttons on the Birthdays and Renewals pages.

## Using one Gmail account for everyone
Set `SHARED_MAIL=1` plus the Gmail SMTP lines in `.env`. The owner and all employees then send through that one Gmail.
Each email shows the employee's name as the sender name, and replies come back to the Gmail inbox. Employees need no
email ID of their own. Create the Google App Password (2-Step Verification must be on) at
https://myaccount.google.com/apppasswords. Gmail SMTP does not work on Render's free plan (SMTP ports are blocked).

## Email drafts that open ready to send (no SMTP needed)
On **Renewals due** and **Write to a client**, the employee who handles the client sees **Gmail** and **Mail app** buttons.
Gmail opens a new message in the employee's own Gmail with the client's address, subject and text already filled in
(it picks the employee's Gmail account if that account is signed in); Mail app opens the computer's default mail program.
The employee reads it and presses Send in their own mail, so it goes from their own address. No App Password, no SMTP
settings and no mail-port problems on Render. The click is recorded in Send history as "email_draft".

## One Gmail for all mail, without App Password or SMTP (Gmail web API)
1. https://console.cloud.google.com -> create a project -> APIs & Services -> Library -> enable **Gmail API**.
2. OAuth consent screen: User type External (or Internal if the account is Google Workspace), app name e.g. "CRM",
   your Gmail as support/developer email, add the scope `.../auth/gmail.send`, add your Gmail as a test user, then set the
   publishing status to **In production** (in Testing status Google expires the sign-in after about 7 days).
3. Credentials -> Create credentials -> OAuth client ID -> type **Desktop app** -> copy the Client ID and Client secret.
4. Run `python get_gmail_token.py CLIENT_ID CLIENT_SECRET`, sign in with the sending Gmail, press Allow (an
   "unverified app" warning is normal for your own app: Advanced -> continue). Copy the lines it prints into `.env`.
5. Restart and run `python test_email.py you@gmail.com`. Every email now leaves from that one Gmail through the CRM, and no
   employee device needs to be signed in.
