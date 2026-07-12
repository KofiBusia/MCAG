"""Platform super-administrator area: tenant registration, approval,
suspension, subscriptions, usage, alerts, templates, support, health."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import text

from mcag.blueprints.helpers import platform_admin_required
from mcag.constants import INSTITUTION_ROLES, ROLE_INSTITUTION_ADMIN
from mcag.extensions import db
from mcag.models import (
    AuditLog, Customer, DuplicateAlert, GlobalDocumentTemplate, Institution,
    Loan, LoginEvent, Subscription, SupportRequest, User,
)
from mcag.models.base import utcnow
from mcag.services.accounting import seed_chart_of_accounts
from mcag.services.audit import log_action
from mcag.utils import password_strength_errors

bp = Blueprint("platform_admin", __name__)


@bp.route("/")
@platform_admin_required
def dashboard():
    stats = {
        "institutions": Institution.query.count(),
        "pending": Institution.query.filter_by(status="pending").count(),
        "active": Institution.query.filter_by(status="active").count(),
        "suspended": Institution.query.filter_by(status="suspended").count(),
        "users": User.query.filter(User.institution_id.isnot(None)).count(),
        "customers": Customer.query.count(),
        "loans": Loan.query.count(),
        "open_support": SupportRequest.query.filter_by(status="open").count(),
        "open_alerts": DuplicateAlert.query.filter_by(status="open").count(),
    }
    recent_logins = (LoginEvent.query.order_by(LoginEvent.occurred_at.desc())
                     .limit(15).all())
    return render_template("platform/dashboard.html", stats=stats,
                           recent_logins=recent_logins)


@bp.route("/institutions")
@platform_admin_required
def institutions():
    records = Institution.query.order_by(Institution.created_at.desc()).all()
    return render_template("platform/institutions.html", institutions=records)


@bp.route("/institutions/new", methods=["GET", "POST"])
@platform_admin_required
def institution_new():
    if request.method == "POST":
        legal_name = (request.form.get("legal_name") or "").strip()
        admin_email = (request.form.get("admin_email") or "").strip().lower()
        admin_name = (request.form.get("admin_name") or "").strip()
        admin_password = request.form.get("admin_password") or ""
        if not legal_name or not admin_email or not admin_name:
            flash("Legal name, administrator name and email are required.", "danger")
            return render_template("platform/institution_form.html")
        errors = password_strength_errors(admin_password)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("platform/institution_form.html")
        if User.query.filter(db.func.lower(User.email) == admin_email).first():
            flash("A user with that email already exists.", "danger")
            return render_template("platform/institution_form.html")

        inst = Institution(
            legal_name=legal_name,
            trading_name=request.form.get("trading_name"),
            mcag_membership_number=request.form.get("mcag_membership_number"),
            email=request.form.get("email"),
            phone_primary=request.form.get("phone_primary"),
            office_address=request.form.get("office_address"),
            status="pending",
        )
        db.session.add(inst)
        db.session.flush()
        admin = User(
            institution_id=inst.id, email=admin_email, full_name=admin_name,
            role=ROLE_INSTITUTION_ADMIN, must_change_password=True,
        )
        admin.set_password(admin_password)
        db.session.add(admin)
        seed_chart_of_accounts(inst)
        log_action("institution_registered", "Institution", inst.id,
                   new_value={"legal_name": legal_name})
        db.session.commit()
        flash("Institution registered (pending approval). The administrator "
              "must change their password at first login.", "success")
        return redirect(url_for("platform_admin.institutions"))
    return render_template("platform/institution_form.html")


@bp.route("/institutions/<int:inst_id>")
@platform_admin_required
def institution_detail(inst_id):
    inst = db.get_or_404(Institution, inst_id)
    # Platform admin sees profile/usage only, never customer or loan details.
    usage = {
        "users": User.query.filter_by(institution_id=inst.id).count(),
        "customers": Customer.query.filter_by(institution_id=inst.id).count(),
        "loans": Loan.query.filter_by(institution_id=inst.id).count(),
    }
    log_action("platform_viewed_institution", "Institution", inst.id)
    db.session.commit()
    subscriptions = Subscription.query.filter_by(institution_id=inst.id).all()
    return render_template("platform/institution_detail.html", inst=inst,
                           usage=usage, subscriptions=subscriptions)


@bp.route("/institutions/<int:inst_id>/status", methods=["POST"])
@platform_admin_required
def institution_status(inst_id):
    inst = db.get_or_404(Institution, inst_id)
    action = request.form.get("action")
    reason = request.form.get("reason") or ""
    old = inst.status
    if action == "approve":
        inst.status = "active"
        inst.approved_at = utcnow()
    elif action == "reject":
        inst.status = "rejected"
    elif action == "suspend":
        inst.status = "suspended"
    elif action == "reactivate":
        inst.status = "active"
    else:
        flash("Unknown action.", "danger")
        return redirect(url_for("platform_admin.institution_detail", inst_id=inst.id))
    inst.status_reason = reason
    log_action("institution_status_changed", "Institution", inst.id,
               old_value=old, new_value=inst.status)
    db.session.commit()
    flash(f"Institution is now {inst.status}.", "success")
    return redirect(url_for("platform_admin.institution_detail", inst_id=inst.id))


@bp.route("/institutions/<int:inst_id>/subscriptions", methods=["POST"])
@platform_admin_required
def subscription_add(inst_id):
    inst = db.get_or_404(Institution, inst_id)
    sub = Subscription(
        institution_id=inst.id,
        plan_name=request.form.get("plan_name") or "Standard",
        amount=request.form.get("amount") or 0,
        start_date=date.fromisoformat(request.form.get("start_date")),
        end_date=(date.fromisoformat(request.form["end_date"])
                  if request.form.get("end_date") else None),
        notes=request.form.get("notes"),
    )
    db.session.add(sub)
    log_action("subscription_added", "Subscription", None,
               new_value={"institution": inst.id, "plan": sub.plan_name})
    db.session.commit()
    flash("Subscription recorded.", "success")
    return redirect(url_for("platform_admin.institution_detail", inst_id=inst.id))


@bp.route("/security-alerts")
@platform_admin_required
def security_alerts():
    events = (LoginEvent.query.filter(LoginEvent.event.in_(["failed", "locked"]))
              .order_by(LoginEvent.occurred_at.desc()).limit(200).all())
    return render_template("platform/security_alerts.html", events=events)


@bp.route("/templates", methods=["GET", "POST"])
@platform_admin_required
def templates():
    if request.method == "POST":
        key = (request.form.get("key") or "").strip()
        record = GlobalDocumentTemplate.query.filter_by(key=key).first()
        if record is None:
            record = GlobalDocumentTemplate(key=key)
            db.session.add(record)
        record.name = request.form.get("name") or key
        record.body = request.form.get("body") or ""
        record.active = bool(request.form.get("active"))
        log_action("global_template_saved", "GlobalDocumentTemplate", key)
        db.session.commit()
        flash("Template saved.", "success")
        return redirect(url_for("platform_admin.templates"))
    records = GlobalDocumentTemplate.query.order_by(GlobalDocumentTemplate.key).all()
    return render_template("platform/templates.html", templates=records)


@bp.route("/support", methods=["GET", "POST"])
@platform_admin_required
def support():
    if request.method == "POST":
        record = db.get_or_404(SupportRequest, request.form.get("request_id", type=int))
        record.response = request.form.get("response")
        record.status = request.form.get("status") or record.status
        if record.status == "resolved":
            record.resolved_at = utcnow()
        db.session.commit()
        flash("Support request updated.", "success")
        return redirect(url_for("platform_admin.support"))
    records = SupportRequest.query.order_by(SupportRequest.created_at.desc()).all()
    return render_template("platform/support.html", requests=records)


@bp.route("/system-health")
@platform_admin_required
def system_health():
    try:
        db.session.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:  # pragma: no cover
        db_status = f"error: {exc}"
    counts = {
        "audit_logs": AuditLog.query.count(),
        "login_events": LoginEvent.query.count(),
    }
    return render_template("platform/system_health.html",
                           db_status=db_status, counts=counts)
