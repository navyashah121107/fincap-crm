from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

PERM_VIEW = {
    "clients": "View clients and family details",
    "policies": "View SIP / insurance / life / fire records",
    "messages": "View message drafts, birthdays/anniversaries and send history",
    "export": "Export data to Excel",
}
PERM_ACT = {
    "add": "Add new clients, policies and message drafts",
    "edit": "Edit existing clients, policies and message drafts",
    "delete": "Delete clients, policies and message drafts",
    "send": "Send messages (birthday, anniversary, renewal, query)",
}
PERMISSIONS = {**PERM_VIEW, **PERM_ACT}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(60), unique=True, nullable=False)
    full_name = db.Column(db.String(120), default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="employee")  # owner | employee
    is_active_user = db.Column(db.Boolean, default=True)
    perms = db.Column(db.String(200), default="")  # comma separated keys of PERMISSIONS
    mobile = db.Column(db.String(20))        # number shown to clients / used for this employee's WhatsApp
    email = db.Column(db.String(150))        # used as Reply-To on emails sent for this employee's clients
    smtp_password = db.Column(db.String(500))  # this mailbox's password (stored encrypted)
    wa_phone_id = db.Column(db.String(40))   # optional: WhatsApp Business API phone-number ID of this employee
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def is_owner(self):
        return self.role == "owner"

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)

    def can(self, perm):
        return self.is_owner or perm in (self.perms or "").split(",")


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    address = db.Column(db.Text)
    mobile = db.Column(db.String(20))
    email = db.Column(db.String(150))
    marital_status = db.Column(db.String(10), default="married")  # married | single
    birthday = db.Column(db.Date)
    anniversary = db.Column(db.Date)
    assigned_to = db.Column(db.Integer, db.ForeignKey("user.id"))  # employee who handles this client
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    family = db.relationship("FamilyMember", backref="client", cascade="all, delete-orphan",
                             order_by="FamilyMember.id")
    sips = db.relationship("Sip", backref="client", cascade="all, delete-orphan")
    insurances = db.relationship("Insurance", backref="client", cascade="all, delete-orphan")
    lifes = db.relationship("LifeInsurance", backref="client", cascade="all, delete-orphan")
    fires = db.relationship("FireInsurance", backref="client", cascade="all, delete-orphan")


class FamilyMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    relation = db.Column(db.String(20))  # spouse, child, father, mother, sibling
    name = db.Column(db.String(150))
    birthday = db.Column(db.Date)


class Sip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    policy_number = db.Column(db.String(80))
    fund_name = db.Column(db.String(150))
    sip_date = db.Column(db.Date)
    policy_type = db.Column(db.String(80))


class Insurance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    category = db.Column(db.String(30), nullable=False)
    insurer = db.Column(db.String(150))
    policy_number = db.Column(db.String(80))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    sum_assured = db.Column(db.Numeric(14, 2))
    premium = db.Column(db.Numeric(14, 2))
    ped_details = db.Column(db.Text)
    remarks = db.Column(db.Text)
    registration_number = db.Column(db.String(50))
    engine_number = db.Column(db.String(60))
    chassis_number = db.Column(db.String(60))
    idv = db.Column(db.Numeric(14, 2))


class LifeInsurance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    insurer = db.Column(db.String(150))
    policy_number = db.Column(db.String(80))
    sum_assured = db.Column(db.Numeric(14, 2))
    start_date = db.Column(db.Date)
    premium_payment_term = db.Column(db.String(60))
    maturity_date = db.Column(db.Date)
    premium = db.Column(db.Numeric(14, 2))
    loading = db.Column(db.Boolean, default=False)
    remarks = db.Column(db.Text)


class FireInsurance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    insurer = db.Column(db.String(150))
    policy_number = db.Column(db.String(80))
    sum_assured = db.Column(db.Numeric(14, 2))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    premium = db.Column(db.Numeric(14, 2))


class MessageTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(20), default="general")  # birthday|anniversary|renewal|query|general
    subject = db.Column(db.String(200), default="")
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MessageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"))
    client_name = db.Column(db.String(150))
    channel = db.Column(db.String(20))
    body = db.Column(db.Text)
    status = db.Column(db.String(20))  # sent | failed | opened
    error = db.Column(db.Text)
    tag = db.Column(db.String(200), index=True)  # used to avoid duplicate auto-wishes
    sent_by = db.Column(db.String(60))
    from_name = db.Column(db.String(160))  # employee (and number) the message went out for
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
