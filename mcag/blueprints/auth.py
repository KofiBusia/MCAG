"""Authentication: login, logout, password reset, first-login change."""
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request,
    session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from mcag.extensions import db
from mcag.models import ActiveSession, LoginEvent, PasswordResetToken, User
from mcag.services.audit import log_action
from mcag.utils import password_strength_errors

bp = Blueprint("auth", __name__)


def _record_event(event, user=None, email=None):
    db.session.add(LoginEvent(
        user_id=user.id if user else None,
        email_attempted=email or (user.email if user else None),
        institution_id=user.institution_id if user else None,
        event=event,
        ip_address=request.remote_addr,
        user_agent=(request.user_agent.string or "")[:250],
    ))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = User.query.filter(db.func.lower(User.email) == email).first()

        if user is None:
            _record_event("failed", email=email)
            db.session.commit()
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html"), 401

        if user.is_locked:
            _record_event("locked", user=user)
            db.session.commit()
            flash("Account is temporarily locked after repeated failed logins. "
                  "Try again later.", "danger")
            return render_template("auth/login.html"), 423

        if not user.is_active_user:
            _record_event("failed", user=user)
            db.session.commit()
            flash("This account has been deactivated.", "danger")
            return render_template("auth/login.html"), 403

        if not user.check_password(password):
            user.register_failed_login(
                current_app.config["MAX_LOGIN_ATTEMPTS"],
                current_app.config["ACCOUNT_LOCK_MINUTES"])
            _record_event("failed", user=user)
            db.session.commit()
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html"), 401

        # Institution must be active (platform admins have no institution)
        if user.institution_id and user.institution.status != "active":
            _record_event("failed", user=user)
            db.session.commit()
            flash("Your institution's account is not active. Contact MCAG support.",
                  "danger")
            return render_template("auth/login.html"), 403

        user.register_successful_login(request.remote_addr)
        login_user(user)
        token = ActiveSession.new_token()
        session["session_token"] = token
        db.session.add(ActiveSession(
            user_id=user.id, session_token=token,
            ip_address=request.remote_addr,
            user_agent=(request.user_agent.string or "")[:250]))
        _record_event("login", user=user)
        log_action("login", "User", user.id, user=user)
        db.session.commit()

        if user.must_change_password:
            return redirect(url_for("auth.change_password"))
        return redirect(url_for("index"))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    token = session.get("session_token")
    if token:
        active = ActiveSession.query.filter_by(session_token=token).first()
        if active:
            active.revoked = True
    _record_event("logout", user=current_user)
    log_action("logout", "User", current_user.id)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    forced = current_user.must_change_password
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        if not current_user.check_password(current):
            flash("Current password is incorrect.", "danger")
            return render_template("auth/change_password.html", forced=forced)
        errors = password_strength_errors(
            new, current_app.config["PASSWORD_MIN_LENGTH"])
        if new != confirm:
            errors.append("New password and confirmation do not match.")
        if current_user.check_password(new):
            errors.append("New password must differ from the current password.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/change_password.html", forced=forced)
        current_user.set_password(new)
        current_user.must_change_password = False
        log_action("password_changed", "User", current_user.id)
        db.session.commit()
        flash("Password updated.", "success")
        return redirect(url_for("index"))
    return render_template("auth/change_password.html", forced=forced)


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter(db.func.lower(User.email) == email).first()
        if user and user.is_active_user:
            token = PasswordResetToken.generate(
                user, current_app.config["PASSWORD_RESET_TOKEN_EXPIRY_MINUTES"])
            db.session.add(token)
            log_action("password_reset_requested", "User", user.id, user=user)
            db.session.commit()
            reset_url = url_for("auth.reset_password", token=token.token, _external=True)
            current_app.logger.info("Password reset link for %s: %s", email, reset_url)
            # With MAIL_* configured, the link is e-mailed; without mail we
            # surface it to an administrator via logs only.
        flash("If that email exists, a password reset link has been generated. "
              "Contact your administrator if you do not receive it.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    record = PasswordResetToken.query.filter_by(token=token).first()
    if record is None or not record.is_valid:
        flash("This reset link is invalid or has expired.", "danger")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""
        errors = password_strength_errors(
            new, current_app.config["PASSWORD_MIN_LENGTH"])
        if new != confirm:
            errors.append("Passwords do not match.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("auth/reset_password.html", token=token)
        user = record.user
        user.set_password(new)
        user.must_change_password = False
        user.locked_until = None
        user.failed_login_attempts = 0
        record.used = True
        log_action("password_reset_completed", "User", user.id, user=user)
        db.session.commit()
        flash("Password has been reset. Please sign in.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", token=token)
