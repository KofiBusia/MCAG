"""Staff users, authentication history, sessions. Customers never get accounts."""
import secrets
from datetime import timedelta

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from mcag.constants import ROLE_PERMISSIONS, ROLE_PLATFORM_ADMIN, ROLE_LABELS
from mcag.extensions import db, login_manager
from mcag.models.base import TimestampMixin, utcnow


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), index=True)
    # NULL institution_id => platform super administrator
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(30))
    role = db.Column(db.String(40), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active_user = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    last_login_ip = db.Column(db.String(64))
    password_changed_at = db.Column(db.DateTime)
    totp_secret = db.Column(db.String(64))  # optional 2FA (reserved)
    approval_limit = db.Column(db.Numeric(18, 2))  # loan approval authority limit

    institution = db.relationship("Institution", back_populates="users")

    # -- authentication -----------------------------------------------------
    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)
        self.password_changed_at = utcnow()

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def register_failed_login(self, max_attempts: int, lock_minutes: int):
        self.failed_login_attempts += 1
        if self.failed_login_attempts >= max_attempts:
            self.locked_until = utcnow() + timedelta(minutes=lock_minutes)
            self.failed_login_attempts = 0

    def register_successful_login(self, ip: str):
        self.failed_login_attempts = 0
        self.locked_until = None
        self.last_login_at = utcnow()
        self.last_login_ip = ip

    # -- authorisation ------------------------------------------------------
    @property
    def is_platform_admin(self) -> bool:
        return self.role == ROLE_PLATFORM_ADMIN

    def can(self, permission: str) -> bool:
        if not self.is_active_user:
            return False
        return permission in ROLE_PERMISSIONS.get(self.role, set())

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    # Flask-Login
    @property
    def is_active(self):
        return self.is_active_user


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class LoginEvent(db.Model):
    """Login history including failed attempts (authentication audit)."""
    __tablename__ = "login_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    email_attempted = db.Column(db.String(255))
    institution_id = db.Column(db.Integer, index=True)
    event = db.Column(db.String(30), nullable=False)  # login | logout | failed | locked
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User")


class ActiveSession(db.Model):
    __tablename__ = "active_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_token = db.Column(db.String(64), nullable=False, unique=True)
    ip_address = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    revoked = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User")

    @staticmethod
    def new_token() -> str:
        return secrets.token_hex(32)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")

    @staticmethod
    def generate(user, minutes: int):
        return PasswordResetToken(
            user_id=user.id,
            token=secrets.token_urlsafe(32),
            expires_at=utcnow() + timedelta(minutes=minutes),
        )

    @property
    def is_valid(self):
        return not self.used and self.expires_at > utcnow()
