import base64
import hashlib
import io
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)
from flask_wtf.csrf import CSRFProtect
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

import messaging
from models import (PERM_ACT, PERM_VIEW, PERMISSIONS, Client, FamilyMember, FireInsurance, Insurance, LifeInsurance,
                    MessageLog, MessageTemplate, Sip, User, db)

COMPANY = os.getenv("COMPANY_NAME", "Shashwat Fincap")
WEBSITE = os.getenv("WEBSITE_URL", "https://www.shashwatfincap.com")

app = Flask(__name__)
db_url = os.getenv("DATABASE_URL", "sqlite:///crm.db")
if "@host/" in db_url or "user:password@" in db_url:
    raise SystemExit("DATABASE_URL in your .env file is still the example text. Paste your real Neon connection "
                     "string there, or delete/comment that line to test with the local file database.")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-me"),
    SQLALCHEMY_DATABASE_URI=db_url,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.getenv("RENDER")),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)
db.init_app(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."


@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


def today():
    """Today's date in India (UTC+5:30), so wishes go out on the right day."""
    return (datetime.utcnow() + timedelta(hours=5, minutes=30)).date()


# --------------------------------------------------------------------------- specs
def F(name, label, typ="text", req=False):
    return (name, label, typ, req)


INS_CATEGORIES = {
    "mediclaim": "Mediclaim",
    "personal_accident": "Personal Accident",
    "two_wheeler": "Two Wheeler",
    "four_wheeler": "Four Wheeler",
    "commercial_vehicle": "Commercial Vehicle",
}
VEHICLE_CATS = {"two_wheeler", "four_wheeler", "commercial_vehicle"}
HEALTH_FIELDS = [
    F("insurer", "Name of insurer", req=True), F("policy_number", "Policy number", req=True),
    F("start_date", "Start date", "date"), F("end_date", "End date", "date"),
    F("sum_assured", "Sum assured", "number"), F("premium", "Premium", "number"),
    F("ped_details", "Details of PED", "textarea"), F("remarks", "Remarks", "textarea"),
]
VEHICLE_FIELDS = [
    F("insurer", "Name of insurer", req=True), F("policy_number", "Policy number", req=True),
    F("start_date", "Start date", "date"), F("end_date", "End date", "date"),
    F("registration_number", "Registration number"), F("engine_number", "Engine number"),
    F("chassis_number", "Chassis number"), F("idv", "IDV", "number"),
]
KINDS = {
    "sip": dict(model=Sip, title="SIP", fields=[
        F("policy_number", "Policy number", req=True), F("fund_name", "Fund name", req=True),
        F("sip_date", "SIP date", "date"), F("policy_type", "Policy type")]),
    "insurance": dict(model=Insurance, title="General Insurance", fields=None),
    "life": dict(model=LifeInsurance, title="Life Insurance", fields=[
        F("insurer", "Name of insurer", req=True), F("policy_number", "Policy number", req=True),
        F("sum_assured", "Sum assured", "number"), F("start_date", "Policy start date", "date"),
        F("premium_payment_term", "Premium payment term"), F("maturity_date", "Date of maturity", "date"),
        F("premium", "Premium", "number"), F("loading", "Loading (if any)", "bool"),
        F("remarks", "Remarks", "textarea")]),
    "fire": dict(model=FireInsurance, title="Fire Insurance", fields=[
        F("insurer", "Name of insurer", req=True), F("policy_number", "Policy number", req=True),
        F("sum_assured", "Sum assured", "number"), F("start_date", "Policy start date", "date"),
        F("end_date", "End date", "date"), F("premium", "Premium", "number")]),
}
EXPIRY_FIELD = {"insurance": "end_date", "fire": "end_date", "life": "maturity_date"}
REL_LABEL = {"spouse": "Spouse", "child": "Child", "father": "Father", "mother": "Mother",
             "sibling": "Sibling"}


def fields_for(kind, category=None):
    if kind == "insurance":
        return VEHICLE_FIELDS if category in VEHICLE_CATS else HEALTH_FIELDS
    return KINDS[kind]["fields"]


def fmt_date(d):
    return d.strftime("%d-%m-%Y") if d else ""


def fmt_money(v):
    return "" if v is None else "₹{:,.2f}".format(v)


app.jinja_env.filters["fdate"] = fmt_date
app.jinja_env.filters["money"] = fmt_money


def pol_title(kind, obj):
    return INS_CATEGORIES.get(obj.category, "Insurance") if kind == "insurance" else KINDS[kind]["title"]


def describe(kind, obj):
    items = []
    for name, label, typ, _ in fields_for(kind, getattr(obj, "category", None)):
        v = getattr(obj, name)
        if v in (None, ""):
            if typ != "bool":
                continue
        if typ == "date":
            v = fmt_date(v)
        elif typ == "number":
            v = fmt_money(v)
        elif typ == "bool":
            v = "Yes" if v else "No"
        items.append((label, v))
    return items


def pol_expiry(kind, obj):
    f = EXPIRY_FIELD.get(kind)
    return getattr(obj, f) if f else None


def pol_name(kind, obj):
    return obj.fund_name if kind == "sip" else obj.insurer


app.jinja_env.globals.update(describe=describe, pol_title=pol_title, pol_expiry=pol_expiry,
                             pol_name=pol_name, INS_CATEGORIES=INS_CATEGORIES, KINDS=KINDS,
                             REL_LABEL=REL_LABEL, PERMISSIONS=PERMISSIONS, PERM_VIEW=PERM_VIEW,
                             PERM_ACT=PERM_ACT)


def parse_form(obj, fields):
    errors = []
    for name, label, typ, req in fields:
        raw = (request.form.get(name) or "").strip()
        if req and not raw:
            errors.append(f"{label} is required.")
            continue
        try:
            if typ == "date":
                val = date.fromisoformat(raw) if raw else None
            elif typ == "number":
                val = Decimal(raw.replace(",", "")) if raw else None
            elif typ == "bool":
                val = raw == "yes"
            else:
                val = raw or None
        except (ValueError, InvalidOperation):
            errors.append(f"{label} is not valid.")
            continue
        setattr(obj, name, val)
    return errors


def pdate(raw):
    raw = (raw or "").strip()
    return date.fromisoformat(raw) if raw else None


# --------------------------------------------------------------------------- permissions
def owner_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_owner:
            abort(403)
        return f(*a, **k)
    return w


def perm_required(*perms):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if not all(current_user.can(p) for p in perms):
                abort(403)
            return f(*a, **k)
        return w
    return deco


def restricted():
    """Employees only ever see the clients assigned to them; the owner sees everyone."""
    return current_user.is_authenticated and not current_user.is_owner


def my_client_ids():
    return select(Client.id).where(Client.assigned_to == current_user.id)


def scoped(model):
    q = model.query
    if not restricted():
        return q
    if model is Client:
        return q.filter(Client.assigned_to == current_user.id)
    if model is MessageLog:
        return q.filter(db.or_(MessageLog.client_id.in_(my_client_ids()),
                               MessageLog.sent_by == current_user.username))
    return q.filter(model.client_id.in_(my_client_ids()))


def check_access(client):
    if restricted() and client.assigned_to != current_user.id:
        abort(403)


def sender_for(client):
    """The employee who handles this client (their number is used); falls back to the owner."""
    u = db.session.get(User, client.assigned_to) if client is not None and client.assigned_to else None
    if u is None or not u.is_active_user:
        u = User.query.filter_by(role="owner").first()
    return u


def sender_ctx(client):
    u = sender_for(client)
    return dict(employee_name=(u.full_name or u.username) if u else "",
                employee_mobile=(u.mobile or "") if u else "")


def from_label(u):
    return (u.full_name or u.username) + (f" ({u.mobile})" if u.mobile else "")


def handler_name(c):
    u = db.session.get(User, c.assigned_to) if c.assigned_to else None
    return (u.full_name or u.username) if u else "Owner"


def staff_users():
    return User.query.filter_by(role="employee").order_by(User.full_name, User.username).all()


app.jinja_env.globals.update(sender_for=sender_for, handler_name=handler_name)


DEFAULT_LOGO = "https://www.shashwatfincap.com/Content/shashwatfincap.com/UploadedImage/RealImage/682logoimg.jpeg"
# SHARED_MAIL=1: owner and all employees send through ONE mail account (e.g. one Gmail); the employee name
# is shown as the sender name and replies come back to that account.
SHARED_MAIL = (os.getenv("SHARED_MAIL") or "").strip().lower() in ("1", "true", "yes")
COMPANY_EMAIL_DOMAIN = (os.getenv("COMPANY_EMAIL_DOMAIN") or "").strip().lower().lstrip("@")


def logo_url():
    """static/logo.png|svg|jpg|jpeg|webp wins; else LOGO_URL from .env; else the logo on the website."""
    for ext in ("png", "svg", "jpg", "jpeg", "webp"):
        if os.path.exists(os.path.join(app.static_folder, f"logo.{ext}")):
            return url_for("static", filename=f"logo.{ext}")
    return os.getenv("LOGO_URL") or DEFAULT_LOGO


def company_email_ok(addr):
    addr = (addr or "").strip().lower()
    if "@" not in addr or " " in addr:
        return False
    if addr.rsplit("@", 1)[1] in ("gamil.com", "gmial.com", "gmai.com", "gmail.co", "gmail.con", "gmal.com"):
        return False
    return not COMPANY_EMAIL_DOMAIN or SHARED_MAIL or addr.endswith("@" + COMPANY_EMAIL_DOMAIN)


COMPANY_EMAIL_HINT = ("Enter a valid company email ID" +
                      (f" ending with @{COMPANY_EMAIL_DOMAIN}" if COMPANY_EMAIL_DOMAIN else "") +
                      ". Check the spelling (for example gmail.com). Emails to the client are sent from it.")


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(app.config["SECRET_KEY"].encode()).digest())
    return Fernet(key)


def enc(text_):
    return _fernet().encrypt(text_.encode()).decode()


def dec(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:  # SECRET_KEY changed or data damaged
        return None


def email_enabled():
    """True if the shared SMTP login is set, or any employee has their own mailbox password saved."""
    if messaging.email_configured():
        return True
    try:
        return db.session.query(User.id).filter(User.smtp_password.isnot(None)).first() is not None
    except Exception:  # noqa
        db.session.rollback()
        return False


def last_errors():
    errs = list(dict.fromkeys(getattr(g, "send_errors", [])))
    return (" Reason: " + " | ".join(errs[:3])) if errs else ""


@app.context_processor
def inject():
    return dict(COMPANY=COMPANY, WEBSITE=WEBSITE, today=today(), logo=logo_url(), shared_mail=SHARED_MAIL,
                email_ok=email_enabled(), wa_api_ok=messaging.whatsapp_api_configured())


@app.after_request
def headers(resp):
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="You don't have permission to do this."), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="That page doesn't exist."), 404


# --------------------------------------------------------------------------- auth
_fails = {}


@app.route("/")
def index():
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = (request.remote_addr, username.lower())
        count, until = _fails.get(key, (0, 0))
        if until > time.time():
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
            return render_template("login.html")
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if user and user.is_active and user.check_password(request.form.get("password", "")):
            _fails.pop(key, None)
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(url_for("dashboard"))
        count += 1
        _fails[key] = (count, time.time() + 600 if count >= 5 else 0)
        flash("Wrong username or password.", "danger")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Current password is wrong.", "danger")
            return redirect(url_for("account"))
        new_pw = request.form.get("new_password", "")
        if new_pw:
            if len(new_pw) < 8 or new_pw != request.form.get("confirm_password"):
                flash("New password must be 8+ characters and match the confirmation.", "danger")
                return redirect(url_for("account"))
            current_user.set_password(new_pw)
        if current_user.is_owner:
            new_name = request.form.get("username", "").strip()
            if new_name and new_name != current_user.username:
                if User.query.filter(func.lower(User.username) == new_name.lower(),
                                     User.id != current_user.id).first():
                    flash("That username is already taken.", "danger")
                    return redirect(url_for("account"))
                current_user.username = new_name
        current_user.full_name = request.form.get("full_name", "").strip()
        if current_user.is_owner:
            new_email = request.form.get("email", "").strip()
            if new_email and not company_email_ok(new_email):
                flash(COMPANY_EMAIL_HINT, "danger")
                return redirect(url_for("account"))
            current_user.mobile = request.form.get("mobile", "").strip() or None
            current_user.email = new_email or None
            current_user.wa_phone_id = request.form.get("wa_phone_id", "").strip() or None
            sp = request.form.get("smtp_password", "").strip()
            if sp:
                current_user.smtp_password = enc(sp)
            elif request.form.get("clear_smtp_password"):
                current_user.smtp_password = None
        db.session.commit()
        flash("Account updated.", "success")
        return redirect(url_for("account"))
    return render_template("account.html")


# --------------------------------------------------------------------------- dashboard
def next_occ(d, ref):
    def at(y):
        try:
            return d.replace(year=y)
        except ValueError:
            return date(y, 2, 28)
    n = at(ref.year)
    return n if n >= ref else at(ref.year + 1)


def collect_occasions(kinds=("birthday", "anniversary")):
    rows = []
    for c in scoped(Client).options(selectinload(Client.family)).order_by(Client.name):
        spouse = next((m.name for m in c.family if m.relation == "spouse" and m.name), None)
        if "birthday" in kinds:
            if c.birthday:
                rows.append(dict(client=c, person=c.name, relation="Self", kind="birthday",
                                 date=c.birthday, spouse=spouse))
            for m in c.family:
                if m.birthday and m.name:
                    rows.append(dict(client=c, person=m.name, relation=REL_LABEL.get(m.relation, m.relation),
                                     kind="birthday", date=m.birthday, spouse=spouse))
        if "anniversary" in kinds and c.anniversary:
            rows.append(dict(client=c, person=c.name + (f" & {spouse}" if spouse else ""),
                             relation="Anniversary", kind="anniversary", date=c.anniversary, spouse=spouse))
    return rows


def occasion_ctx(r):
    c = r["client"]
    self_ = r["relation"] == "Self"
    return dict(name=c.name, person=r["person"], relation=r["relation"],
                occasion="Birthday" if r["kind"] == "birthday" else "Wedding Anniversary",
                wish_for="you" if self_ else r["person"],
                couple=f"you and {r['spouse']}" if r["spouse"] else "you",
                spouse=r["spouse"] or "", company=COMPANY, **sender_ctx(c))


def occ_key(r):
    return f"{r['client'].id}|{r['kind']}|{r['person']}"


def policy_rows(kinds=("insurance", "fire", "life"), start=None, end=None):
    rows = []
    for kind in kinds:
        for p in scoped(KINDS[kind]["model"]).all():
            e = pol_expiry(kind, p)
            if e and (start is None or e >= start) and (end is None or e <= end):
                rows.append(dict(kind=kind, p=p, client=p.client, expiry=e,
                                 days=(e - today()).days))
    rows.sort(key=lambda r: r["expiry"])
    return rows


def policy_ctx(kind, p):
    e = pol_expiry(kind, p)
    return dict(policy_type=pol_title(kind, p), policy_number=p.policy_number or "",
                insurer=pol_name(kind, p) or "", expiry_date=fmt_date(e),
                premium=fmt_money(getattr(p, "premium", None)) if hasattr(p, "premium") else "")


@app.route("/dashboard")
@login_required
def dashboard():
    t = today()
    stats = dict(
        clients=scoped(Client).count(), sip=scoped(Sip).count(), life=scoped(LifeInsurance).count(),
        fire=scoped(FireInsurance).count(), insurance=scoped(Insurance).count(),
        employees=User.query.filter_by(role="employee").count(),
        by_cat={k: scoped(Insurance).filter(Insurance.category == k).count() for k in INS_CATEGORIES},
    )
    upcoming = []
    if current_user.can("clients"):
        for r in collect_occasions():
            n = next_occ(r["date"], t)
            if (n - t).days <= 7:
                r["next"] = n
                upcoming.append(r)
        upcoming.sort(key=lambda r: r["next"])
    renewals = policy_rows(start=t, end=t + timedelta(days=30))[:8] if current_user.can("policies") else []
    logs = scoped(MessageLog).order_by(MessageLog.sent_at.desc()).limit(6).all() if current_user.can("messages") else []
    return render_template("dashboard.html", stats=stats, upcoming=upcoming, renewals=renewals, logs=logs)


# --------------------------------------------------------------------------- clients
def build_slots(c, form=None):
    married_spec = [("spouse", "Spouse")] + [("child", f"Child {i}") for i in range(1, 5)]
    single_spec = [("father", "Father"), ("mother", "Mother")] + [("sibling", f"Sibling {i}") for i in range(1, 5)]

    def make(prefix, spec):
        if form is not None and form.getlist(prefix + "name"):
            names, bds = form.getlist(prefix + "name"), form.getlist(prefix + "bday")
            return [dict(rel=r, label=l, name=names[i] if i < len(names) else "",
                         bday=bds[i] if i < len(bds) else "") for i, (r, l) in enumerate(spec)]
        used, out = {}, []
        for r, l in spec:
            pool = [m for m in (c.family if c else []) if m.relation == r]
            i = used.get(r, 0)
            used[r] = i + 1
            m = pool[i] if i < len(pool) else None
            out.append(dict(rel=r, label=l, name=m.name if m else "",
                            bday=m.birthday.isoformat() if m and m.birthday else ""))
        return out

    return dict(married=make("m_", married_spec), single=make("s_", single_spec))


def client_form_data(c):
    if request.method == "POST":
        return request.form
    if c is None:
        return {"marital_status": "married"}
    return dict(name=c.name, address=c.address or "", mobile=c.mobile or "", email=c.email or "",
                marital_status=c.marital_status or "married",
                birthday=c.birthday.isoformat() if c.birthday else "",
                anniversary=c.anniversary.isoformat() if c.anniversary else "",
                assigned_to=str(c.assigned_to or ""))


def save_client(c, creating=False):
    errors = []
    f = request.form
    c.name = f.get("name", "").strip()
    if not c.name:
        errors.append("Client name is required.")
    c.address = f.get("address", "").strip() or None
    c.mobile = f.get("mobile", "").strip() or None
    c.email = f.get("email", "").strip() or None
    c.marital_status = "single" if f.get("marital_status") == "single" else "married"
    try:
        c.birthday = pdate(f.get("birthday"))
        c.anniversary = pdate(f.get("anniversary")) if c.marital_status == "married" else None
    except ValueError:
        errors.append("One of the dates is not valid.")
    prefix = "m_" if c.marital_status == "married" else "s_"
    members = []
    try:
        for rel, name, bday in zip(f.getlist(prefix + "rel"), f.getlist(prefix + "name"),
                                   f.getlist(prefix + "bday")):
            name = name.strip()
            if name or bday:
                members.append(FamilyMember(relation=rel, name=name or None, birthday=pdate(bday)))
    except ValueError:
        errors.append("A family member's date is not valid.")
    if current_user.is_owner:
        raw = f.get("assigned_to", "")
        emp = db.session.get(User, int(raw)) if raw.isdigit() else None
        c.assigned_to = emp.id if emp and not emp.is_owner else None
    elif creating:
        c.assigned_to = current_user.id
    if not errors:
        c.family[:] = members
    return errors


@app.route("/clients")
@perm_required("clients")
def clients():
    q = request.args.get("q", "").strip()
    handler = request.args.get("handler", "")
    query = scoped(Client)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Client.name.ilike(like), Client.mobile.ilike(like),
                                    Client.email.ilike(like)))
    if current_user.is_owner and handler:
        if handler == "none":
            query = query.filter(Client.assigned_to.is_(None))
        elif handler.isdigit():
            query = query.filter(Client.assigned_to == int(handler))
    return render_template("clients.html", clients=query.order_by(Client.name).limit(500).all(), q=q,
                           handler=handler, staff=staff_users() if current_user.is_owner else [])


@app.route("/clients/assign", methods=["POST"])
@owner_required
def clients_assign():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    target = request.form.get("employee_id", "")
    emp = db.session.get(User, int(target)) if target.isdigit() else None
    if not ids or (target != "none" and (emp is None or emp.is_owner)):
        flash("Tick the clients and choose an employee (or Owner) to hand them to.", "danger")
        return redirect(url_for("clients"))
    Client.query.filter(Client.id.in_(ids)).update({"assigned_to": emp.id if emp else None},
                                                   synchronize_session=False)
    db.session.commit()
    flash(f"{len(ids)} client(s) now handled by {(emp.full_name or emp.username) if emp else 'the owner'}. "
          "Their messages will go out with that person's number.", "success")
    return redirect(url_for("clients"))


@app.route("/clients/new", methods=["GET", "POST"])
@perm_required("add", "clients")
def client_new():
    c = Client()
    if request.method == "POST":
        errors = save_client(c, creating=True)
        if not errors:
            db.session.add(c)
            db.session.commit()
            flash("Client saved. You can now add SIP and insurance details.", "success")
            return redirect(url_for("client_detail", cid=c.id))
        for e in errors:
            flash(e, "danger")
    return render_template("client_form.html", c=None, data=client_form_data(None), staff=staff_users(),
                           slots=build_slots(None, request.form if request.method == "POST" else None))


@app.route("/clients/<int:cid>/edit", methods=["GET", "POST"])
@perm_required("edit", "clients")
def client_edit(cid):
    c = db.get_or_404(Client, cid)
    check_access(c)
    if request.method == "POST":
        errors = save_client(c)
        if not errors:
            db.session.commit()
            flash("Client updated.", "success")
            return redirect(url_for("client_detail", cid=c.id))
        db.session.rollback()
        for e in errors:
            flash(e, "danger")
    return render_template("client_form.html", c=c, data=client_form_data(c), staff=staff_users(),
                           slots=build_slots(c, request.form if request.method == "POST" else None))


@app.route("/clients/<int:cid>")
@perm_required("clients")
def client_detail(cid):
    c = db.get_or_404(Client, cid)
    check_access(c)
    return render_template("client_detail.html", c=c)


@app.route("/clients/<int:cid>/delete", methods=["POST"])
@perm_required("delete", "clients")
def client_delete(cid):
    c = db.get_or_404(Client, cid)
    check_access(c)
    MessageLog.query.filter_by(client_id=cid).update({"client_id": None})
    db.session.delete(c)
    db.session.commit()
    flash("Client and all linked records deleted.", "success")
    return redirect(url_for("clients"))


# --------------------------------------------------------------------------- policies
@app.route("/clients/<int:cid>/<kind>/new", methods=["GET", "POST"])
@perm_required("add", "policies")
def policy_new(cid, kind):
    if kind not in KINDS:
        abort(404)
    c = db.get_or_404(Client, cid)
    check_access(c)
    category = request.values.get("category")
    if kind == "insurance" and category not in INS_CATEGORIES:
        category = None
    obj = KINDS[kind]["model"](client_id=c.id)
    if kind == "insurance":
        obj.category = category
    if request.method == "POST" and (kind != "insurance" or category):
        errors = parse_form(obj, fields_for(kind, category))
        if not errors:
            db.session.add(obj)
            db.session.commit()
            flash(f"{pol_title(kind, obj)} saved.", "success")
            return redirect(url_for("client_detail", cid=c.id))
        for e in errors:
            flash(e, "danger")
    return render_template("policy_form.html", c=c, kind=kind, obj=None, category=category,
                           fields=fields_for(kind, category) if (kind != "insurance" or category) else [],
                           values=request.form if request.method == "POST" else {})


@app.route("/policies/<kind>/<int:pid>/edit", methods=["GET", "POST"])
@perm_required("edit", "policies")
def policy_edit(kind, pid):
    if kind not in KINDS:
        abort(404)
    obj = db.get_or_404(KINDS[kind]["model"], pid)
    check_access(obj.client)
    category = getattr(obj, "category", None)
    fields = fields_for(kind, category)
    if request.method == "POST":
        errors = parse_form(obj, fields)
        if not errors:
            db.session.commit()
            flash("Record updated.", "success")
            return redirect(url_for("client_detail", cid=obj.client_id))
        db.session.rollback()
        for e in errors:
            flash(e, "danger")
    values = request.form if request.method == "POST" else {
        n: ("yes" if getattr(obj, n) else "no") if t == "bool" else
           (getattr(obj, n).isoformat() if t == "date" and getattr(obj, n) else
            ("" if getattr(obj, n) is None else str(getattr(obj, n))))
        for n, _, t, _ in fields}
    return render_template("policy_form.html", c=obj.client, kind=kind, obj=obj, category=category,
                           fields=fields, values=values)


@app.route("/policies/<kind>/<int:pid>/delete", methods=["POST"])
@perm_required("delete", "policies")
def policy_delete(kind, pid):
    if kind not in KINDS:
        abort(404)
    obj = db.get_or_404(KINDS[kind]["model"], pid)
    check_access(obj.client)
    cid = obj.client_id
    db.session.delete(obj)
    db.session.commit()
    flash("Record deleted.", "success")
    return redirect(url_for("client_detail", cid=cid))


@app.route("/renewals", methods=["GET", "POST"])
@perm_required("policies")
def renewals():
    days = request.values.get("days", 30, type=int)
    rows = policy_rows(start=today() - timedelta(days=15), end=today() + timedelta(days=days))
    templates = MessageTemplate.query.order_by(MessageTemplate.category, MessageTemplate.title).all()
    tid = request.values.get("template_id", type=int)
    tpl = db.session.get(MessageTemplate, tid) if tid else None
    if not tpl:
        tpl = next((x for x in templates if x.category == "renewal"), templates[0] if templates else None)
    done_tags = opened_today()
    email_tags = opened_today("em")
    for r in rows:
        r["key"] = f"{r['kind']}:{r['p'].id}"
        r["ctx"] = dict(name=r["client"].name, company=COMPANY, **sender_ctx(r["client"]), **policy_ctx(r["kind"], r["p"]))
        r["sender"] = sender_for(r["client"])
        r["wa"] = messaging.normalize_mobile(r["client"].mobile)
        r["done"] = f"wa:{r['key']}:{today().isoformat()}" in done_tags
        r["email_done"] = f"em:{r['key']}:{today().isoformat()}" in email_tags
    if request.method == "POST":
        if not current_user.can("send"):
            abort(403)
        chosen = set(request.form.getlist("sel"))
        body, subject = request.form.get("body", ""), request.form.get("subject", "")
        channels = request.form.getlist("channels")
        if not chosen or not channels or not body.strip():
            flash("Select at least one policy, one channel and write the message.", "danger")
        else:
            ok = total = 0
            for r in rows:
                if r["key"] in chosen:
                    total += 1
                    ok += deliver(r["client"], messaging.render(body, r["ctx"]),
                                  messaging.render(subject, r["ctx"]), channels,
                                  wa_category="renewal", ctx=r["ctx"])
            flash(f"Sent {ok} message(s) for {total} selected polic{'y' if total == 1 else 'ies'}. "
                  "See Send history for failures." + last_errors(), "success" if ok else "danger")
        return redirect(url_for("renewals", days=days, template_id=tpl.id if tpl else None))
    return render_template("renewals.html", rows=rows, days=days, templates=templates, tpl=tpl)


# --------------------------------------------------------------------------- messages
@app.route("/messages/templates")
@perm_required("messages")
def templates_list():
    return render_template("templates_list.html", items=MessageTemplate.query.order_by(
        MessageTemplate.category, MessageTemplate.title).all())


@app.route("/messages/templates/new", methods=["GET", "POST"])
@app.route("/messages/templates/<int:tid>/edit", methods=["GET", "POST"])
@perm_required("messages")
def template_edit(tid=None):
    if not current_user.can("edit" if tid else "add"):
        abort(403)
    t = db.get_or_404(MessageTemplate, tid) if tid else MessageTemplate()
    if request.method == "POST":
        t.title = request.form.get("title", "").strip()
        t.category = request.form.get("category", "general")
        t.subject = request.form.get("subject", "").strip()
        t.body = request.form.get("body", "").strip()
        if not t.title or not t.body:
            flash("Title and message text are required.", "danger")
        else:
            db.session.add(t)
            db.session.commit()
            flash("Draft saved.", "success")
            return redirect(url_for("templates_list"))
    return render_template("template_form.html", t=t)


@app.route("/messages/templates/<int:tid>/delete", methods=["POST"])
@perm_required("delete", "messages")
def template_delete(tid):
    db.session.delete(db.get_or_404(MessageTemplate, tid))
    db.session.commit()
    flash("Draft deleted.", "success")
    return redirect(url_for("templates_list"))


def deliver(client, body, subject, channels, tag=None, wa_category=None, ctx=None):
    """Send for one client, as the employee who handles that client (owner if unassigned):
    email from that employee's company mailbox, WhatsApp straight from that employee's company number."""
    sent = 0
    if wa_category in ("birthday", "anniversary"):  # wishes go by WhatsApp only; email is for renewals
        channels = [c for c in channels if c != "email"]
    who = current_user.username if current_user.is_authenticated else "auto"
    sender = sender_for(client)
    label = from_label(sender) if sender else None
    if not hasattr(g, "send_errors"):
        g.send_errors = []
    for ch in channels:
        ctag = f"{tag}:{ch}" if tag else None
        if ctag and MessageLog.query.filter_by(tag=ctag, status="sent").first():
            continue  # this automatic message was already sent on this channel
        if ch == "email" and SHARED_MAIL:
            ok, err = messaging.send_email(client.email, subject, body,
                                           reply_to=sender.email if sender and sender.email else None,
                                           from_name=(sender.full_name or sender.username) if sender else None)
        elif ch == "email":
            from_email = sender.email if sender and sender.email else None
            mailbox_pw = dec(sender.smtp_password) if sender and sender.smtp_password else None
            if sender and not sender.is_owner and not from_email:
                ok, err = False, (f"No company email ID is set for {sender.full_name or sender.username}. "
                                  "Add it under Employees.")
            elif sender and sender.smtp_password and mailbox_pw is None:
                ok, err = False, "The saved mailbox password cannot be read. Re-enter it under Employees."
            else:
                ok, err = messaging.send_email(
                    client.email, subject, body, from_email=from_email,
                    from_name=(sender.full_name or sender.username) if sender else None,
                    login_user=from_email if mailbox_pw else None, login_password=mailbox_pw)
        elif ch == "whatsapp_api":
            # always the handling employee's own company number; only the owner may use the default one
            pid = sender.wa_phone_id if sender and sender.wa_phone_id else (
                os.getenv("WHATSAPP_PHONE_ID") if sender and sender.is_owner else None)
            tpl_info = messaging.wa_template(wa_category, ctx or {}) if wa_category else None
            ok, err = messaging.send_whatsapp_api(client.mobile, body, phone_id=pid, template=tpl_info)
        else:
            continue
        if not ok and err:
            g.send_errors.append(f"{client.name}: {err}")
        db.session.add(MessageLog(client_id=client.id, client_name=client.name, channel=ch, body=body,
                                  status="sent" if ok else "failed", error=err or None, tag=ctag,
                                  sent_by=who, from_name=label))
        sent += 1 if ok else 0
    db.session.commit()
    return sent


@app.route("/messages/occasions", methods=["GET", "POST"])
@perm_required("messages")
def occasions():
    kind = request.values.get("type", "both")
    month = request.values.get("month", today().month, type=int)
    kinds = ("birthday", "anniversary") if kind == "both" else (kind,)
    rows = [r for r in collect_occasions(kinds) if r["date"].month == month]
    rows.sort(key=lambda r: (r["date"].day, r["client"].name))
    templates = MessageTemplate.query.order_by(MessageTemplate.category, MessageTemplate.title).all()
    tid = request.values.get("template_id", type=int)
    tpl = db.session.get(MessageTemplate, tid) if tid else None
    if not tpl:
        pref = "anniversary" if kind == "anniversary" else "birthday"
        tpl = next((x for x in templates if x.category == pref), None)

    if request.method == "POST":
        if not current_user.can("send"):
            abort(403)
        chosen = set(request.form.getlist("sel"))
        body, subject = request.form.get("body", ""), request.form.get("subject", "")
        channels = request.form.getlist("channels")
        if not chosen or not channels or not body.strip():
            flash("Select at least one person, one channel and write the message.", "danger")
        else:
            ok = total = 0
            for r in rows:
                if occ_key(r) in chosen:
                    total += 1
                    oc = occasion_ctx(r)
                    ok += deliver(r["client"], messaging.render(body, oc), messaging.render(subject, oc),
                                  channels, wa_category=r["kind"], ctx=oc)
            flash(f"Sent {ok} message(s) for {total} selected row(s). See Send history for failures."
                  + last_errors(), "success" if ok else "danger")
        return redirect(url_for("occasions", month=month, type=kind, template_id=tpl.id if tpl else None))

    done_tags = opened_today()
    for r in rows:
        r["key"] = occ_key(r)
        r["ctx"] = occasion_ctx(r)
        r["wa"] = messaging.normalize_mobile(r["client"].mobile)
        r["sender"] = sender_for(r["client"])
        r["done"] = f"wa:{r['key']}:{today().isoformat()}" in done_tags
    return render_template("occasions.html", rows=rows, month=month, kind=kind,
                           templates=templates, tpl=tpl)


@app.route("/messages/compose", methods=["GET", "POST"])
@perm_required("messages")
def compose():
    cid = request.values.get("client_id", type=int)
    pkind, pid = request.values.get("pkind"), request.values.get("pid", type=int)
    client = db.session.get(Client, cid) if cid else None
    policy = None
    if pkind in KINDS and pid:
        policy = db.session.get(KINDS[pkind]["model"], pid)
        if policy:
            client = policy.client
    templates = MessageTemplate.query.order_by(MessageTemplate.category, MessageTemplate.title).all()
    tid = request.values.get("template_id", type=int)
    tpl = db.session.get(MessageTemplate, tid) if tid else None
    if not tpl and templates:
        want = "renewal" if policy else "general"
        tpl = next((x for x in templates if x.category == want), templates[0])
    if client:
        check_access(client)
    ctx = {}
    if client:
        ctx = dict(name=client.name, company=COMPANY, **sender_ctx(client))
        if policy:
            ctx.update(policy_ctx(pkind, policy))
    if request.method == "POST":
        if not current_user.can("send"):
            abort(403)
        channels = request.form.getlist("channels")
        body = request.form.get("body", "").strip()
        if not client or not channels or not body:
            flash("Choose a client, at least one channel and write a message.", "danger")
        else:
            ok = deliver(client, body, request.form.get("subject", ""), channels)
            flash("Message sent." if ok else "Sending failed." + last_errors(),
                  "success" if ok else "danger")
            return redirect(url_for("compose", client_id=client.id))
    body = messaging.render(tpl.body, ctx) if tpl else ""
    subject = messaging.render(tpl.subject, ctx) if tpl else ""
    return render_template("compose.html", client=client, policy=policy, pkind=pkind, templates=templates,
                           tpl=tpl, body=request.form.get("body", body),
                           subject=request.form.get("subject", subject),
                           all_clients=scoped(Client).order_by(Client.name).all() if not client else [],
                           sender=sender_for(client) if client else None)


def opened_today(prefix="wa"):
    """Tags of the Open-WhatsApp (wa) / Open-email-draft (em) clicks made today, so finished rows can be ticked off."""
    suffix = ":" + today().isoformat()
    return {t for (t,) in db.session.query(MessageLog.tag).filter(MessageLog.tag.like(prefix + ":%" + suffix)).all()}


@app.route("/messages/log-open", methods=["POST"])
@perm_required("send", "messages")
def log_open():
    d = request.get_json(silent=True) or {}
    c = db.session.get(Client, d.get("client_id") or 0)
    if c:
        check_access(c)
        key = (d.get("key") or "")[:150]
        is_mail = d.get("channel") == "email"
        wtag = f"{'em' if is_mail else 'wa'}:{key}:{today().isoformat()}" if key else None
        db.session.add(MessageLog(client_id=c.id, client_name=c.name, channel="email_draft" if is_mail else "whatsapp_link", tag=wtag,
                                  body=(d.get("body") or "")[:2000], status="opened",
                                  sent_by=current_user.username, from_name=from_label(current_user)))
        db.session.commit()
    return jsonify(ok=True)


@app.route("/messages/logs")
@perm_required("messages")
def logs():
    return render_template("logs.html", items=scoped(MessageLog).order_by(MessageLog.sent_at.desc()).limit(300).all())


@app.route("/cron/daily")
def cron_daily():
    """Hit once a day (e.g. cron-job.org) to auto-send today's birthday/anniversary wishes."""
    if request.args.get("key") != os.getenv("CRON_KEY") or not os.getenv("CRON_KEY"):
        abort(403)
    t = today()
    tpls = {c: MessageTemplate.query.filter_by(category=c).order_by(MessageTemplate.id).first()
            for c in ("birthday", "anniversary")}
    channels = (["email"] if email_enabled() else []) + \
               (["whatsapp_api"] if messaging.whatsapp_api_configured() else [])
    wish_channels = ["whatsapp_api"] if messaging.whatsapp_api_configured() else []  # wishes: WhatsApp only
    sent = skipped = 0
    for r in collect_occasions():
        tpl = tpls.get(r["kind"])
        if not tpl or next_occ(r["date"], t) != t or not wish_channels:
            continue
        tag = f"auto:{r['kind']}:{r['client'].id}:{r['person']}:{t.isoformat()}"
        if MessageLog.query.filter(MessageLog.tag.like(tag + ":%"), MessageLog.status == "sent").count() >= len(wish_channels):
            skipped += 1
            continue
        ctx = occasion_ctx(r)
        sent += deliver(r["client"], messaging.render(tpl.body, ctx), messaging.render(tpl.subject, ctx),
                        wish_channels, tag=tag, wa_category=r["kind"], ctx=ctx)
    # Renewal reminders: sent N days before the policy ends (default 30, 7 and 1 days before)
    rem_days = {int(x) for x in os.getenv("RENEWAL_REMINDER_DAYS", "30,7,1").split(",") if x.strip().isdigit()}
    rtpl = MessageTemplate.query.filter_by(category="renewal").order_by(MessageTemplate.id).first()
    renewal_sent = 0
    if rtpl and channels and rem_days:
        for r in policy_rows(start=t, end=t + timedelta(days=max(rem_days))):
            if r["days"] not in rem_days:
                continue
            tag = f"auto:renewal:{r['kind']}:{r['p'].id}:{r['days']}"
            if MessageLog.query.filter(MessageLog.tag.like(tag + ":%"), MessageLog.status == "sent").count() >= len(channels):
                continue
            ctx = dict(name=r["client"].name, company=COMPANY, **sender_ctx(r["client"]))
            ctx.update(policy_ctx(r["kind"], r["p"]))
            renewal_sent += deliver(r["client"], messaging.render(rtpl.body, ctx),
                                    messaging.render(rtpl.subject, ctx), channels, tag=tag,
                                    wa_category="renewal", ctx=ctx)
    return jsonify(date=t.isoformat(), wishes_sent=sent, wishes_already_sent=skipped,
                   renewals_sent=renewal_sent, wish_channels=wish_channels, renewal_channels=channels)


# --------------------------------------------------------------------------- employees
@app.route("/employees")
@owner_required
def employees():
    counts = dict(db.session.query(Client.assigned_to, func.count(Client.id)).group_by(Client.assigned_to).all())
    return render_template("employees.html", users=User.query.order_by(User.role, User.username).all(),
                           counts=counts)


@app.route("/employees/new", methods=["GET", "POST"])
@app.route("/employees/<int:uid>/edit", methods=["GET", "POST"])
@owner_required
def employee_edit(uid=None):
    u = db.get_or_404(User, uid) if uid else User(role="employee", is_active_user=True)
    if u.is_owner:
        abort(403)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        clash = User.query.filter(func.lower(User.username) == username.lower(), User.id != (u.id or 0)).first()
        if not username or clash:
            flash("Username is required and must be unique.", "danger")
        elif not uid and len(pw) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif uid and pw and len(pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif not SHARED_MAIL and not company_email_ok(request.form.get("email", "")):
            flash(COMPANY_EMAIL_HINT, "danger")
        elif len(messaging.normalize_mobile(request.form.get("mobile", ""))) < 10:
            flash("Enter this employee's mobile / WhatsApp number. It is used for all messages to their clients.",
                  "danger")
        else:
            u.username = username
            u.mobile = request.form.get("mobile", "").strip()
            u.email = request.form.get("email", "").strip() or None
            u.wa_phone_id = request.form.get("wa_phone_id", "").strip() or None
            sp = request.form.get("smtp_password", "").strip()
            if sp:
                u.smtp_password = enc(sp)
            elif request.form.get("clear_smtp_password"):
                u.smtp_password = None
            u.full_name = request.form.get("full_name", "").strip()
            u.is_active_user = bool(request.form.get("is_active"))
            u.perms = ",".join(p for p in PERMISSIONS if request.form.get("perm_" + p))
            if pw:
                u.set_password(pw)
            db.session.add(u)
            db.session.commit()
            flash("Employee saved.", "success")
            return redirect(url_for("employees"))
    return render_template("employee_form.html", u=u)


@app.route("/employees/<int:uid>/delete", methods=["POST"])
@owner_required
def employee_delete(uid):
    u = db.get_or_404(User, uid)
    if u.is_owner:
        abort(403)
    Client.query.filter_by(assigned_to=u.id).update({"assigned_to": None})
    db.session.delete(u)
    db.session.commit()
    flash("Employee removed. Their clients are now handled by the owner.", "success")
    return redirect(url_for("employees"))


# --------------------------------------------------------------------------- export
INS_ALL = [("category", "Category")] + [(n, l) for n, l, _, _ in HEALTH_FIELDS] + \
          [(n, l) for n, l, _, _ in VEHICLE_FIELDS if n not in {x[0] for x in HEALTH_FIELDS}]


@app.route("/export/<what>.xlsx")
@perm_required("export")
def export(what):
    wb = Workbook()
    ws = wb.active
    ws.title = what.title()
    if what == "clients":
        if not current_user.can("clients"):
            abort(403)
        head = ["Name", "Mobile", "Email", "Address", "Status", "Birthday", "Anniversary", "Family", "Handled by"]
        rows = [[c.name, c.mobile, c.email, c.address, c.marital_status, c.birthday, c.anniversary,
                 "; ".join(f"{REL_LABEL.get(m.relation, m.relation)}: {m.name or ''} ({fmt_date(m.birthday)})"
                           for m in c.family), handler_name(c)] for c in scoped(Client).order_by(Client.name)]
    elif what in KINDS:
        if not current_user.can("policies"):
            abort(403)
        model = KINDS[what]["model"]
        cols = INS_ALL if what == "insurance" else [(n, l) for n, l, _, _ in KINDS[what]["fields"]]
        head = ["Client", "Mobile", "Email", "Handled by"] + [l for _, l in cols]
        q = scoped(model).join(Client).order_by(Client.name)
        if what == "insurance" and request.args.get("category") in INS_CATEGORIES:
            q = q.filter(Insurance.category == request.args["category"])
        rows = []
        for p in q:
            vals = []
            for n, _ in cols:
                v = getattr(p, n)
                vals.append(INS_CATEGORIES.get(v, v) if n == "category" else
                            ("Yes" if v else "No") if isinstance(v, bool) else
                            float(v) if isinstance(v, Decimal) else v)
            rows.append([p.client.name, p.client.mobile, p.client.email, handler_name(p.client)] + vals)
    else:
        abort(404)
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0F3D4C")
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, date):
                cell.number_format = "DD-MM-YYYY"
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(40, max(12, max(len(str(c.value or "")) for c in col) + 2))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f"{what}-{today().isoformat()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- startup
DEFAULT_TEMPLATES = [
    ("Birthday wish", "birthday", "Happy Birthday from {company}",
     "Dear {name},\n\nWishing {wish_for} a very happy birthday! May the year ahead bring good health, "
     "happiness and prosperity.\n\nWarm regards,\n{employee_name} ({employee_mobile})\n{company}"),
    ("Anniversary wish", "anniversary", "Happy Anniversary from {company}",
     "Dear {name},\n\nWishing {couple} a very happy wedding anniversary! May your bond keep growing "
     "stronger every year.\n\nWarm regards,\n{employee_name} ({employee_mobile})\n{company}"),
    ("Policy renewal reminder", "renewal", "Your {policy_type} policy is due for renewal",
     "Dear {name},\n\nYour {policy_type} policy no. {policy_number} with {insurer} is due on {expiry_date}. "
     "Please contact us at your earliest to renew it on time and keep your cover uninterrupted.\n\n"
     "Regards,\n{employee_name} ({employee_mobile})\n{company}"),
    ("General query reply", "query", "Regarding your query",
     "Dear {name},\n\nThank you for reaching out. We have noted your query and will get back to you shortly.\n\n"
     "Regards,\n{employee_name} ({employee_mobile})\n{company}"),
]


def ensure_columns():
    """Adds columns introduced in newer versions to tables that already exist (no manual SQL needed)."""
    wanted = {"user": [("mobile", "VARCHAR(20)"), ("email", "VARCHAR(150)"), ("wa_phone_id", "VARCHAR(40)"),
             ("smtp_password", "VARCHAR(500)")],
              "client": [("assigned_to", "INTEGER")],
              "message_log": [("from_name", "VARCHAR(160)")]}
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, typ in cols:
            if name in have:
                continue
            try:
                db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {typ}'))
                db.session.commit()
            except Exception:  # another worker may have added it a moment ago
                db.session.rollback()


def init_db():
    with app.app_context():
        db.create_all()
        ensure_columns()
        if not User.query.filter_by(role="owner").first():
            u, p = os.getenv("OWNER_USERNAME"), os.getenv("OWNER_PASSWORD")
            if u and p:
                owner = User(username=u, full_name="Owner", role="owner")
                owner.set_password(p)
                db.session.add(owner)
                db.session.commit()
        if MessageTemplate.query.count() == 0:
            for title, cat, subj, body in DEFAULT_TEMPLATES:
                db.session.add(MessageTemplate(title=title, category=cat, subject=subj, body=body))
            db.session.commit()


try:
    init_db()
except OperationalError as exc:  # database not reachable: show a short, useful message instead of a long trace
    raise SystemExit("Cannot connect to the database: " + str(exc.orig).strip().splitlines()[0] +
                     "\nCheck your internet / DNS, and the DATABASE_URL line in .env "
                     "(comment it out with # to use the local file database for testing).")

if __name__ == "__main__":
    app.run(debug=True)
