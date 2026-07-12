"""Institution profile, staff users, collection zones, settings."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import (
    INSTITUTION_ROLES, P_MANAGE_SETTINGS, P_MANAGE_USERS, P_VIEW,
)
from mcag.extensions import db
from mcag.models import CollectionZone, LoginEvent, SupportRequest, User
from mcag.services.audit import log_action
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import password_strength_errors

bp = Blueprint("institution", __name__)

PROFILE_FIELDS = [
    "legal_name", "trading_name", "business_registration_number", "tin",
    "mcag_membership_number", "bog_licence_reference", "office_address",
    "digital_address", "area_of_operation", "phone_primary", "phone_secondary",
    "email", "proprietor_name", "manager_name", "principal_bank",
    "bank_account_name", "bank_account_number", "dpc_registration",
    "credit_bureau_relationship", "auditor_name", "accountant_name",
]
DATE_FIELDS = ["date_operations_commenced", "regulatory_renewal_date", "mcag_renewal_date"]


@bp.route("/profile", methods=["GET", "POST"])
@permission_required(P_VIEW)
def profile():
    inst = current_user.institution
    if request.method == "POST":
        if not current_user.can(P_MANAGE_SETTINGS):
            flash("You do not have permission to edit the institution profile.", "danger")
            return redirect(url_for("institution.profile"))
        old = {f: getattr(inst, f) for f in PROFILE_FIELDS}
        for field in PROFILE_FIELDS:
            if field in request.form:
                setattr(inst, field, request.form.get(field) or None)
        for field in DATE_FIELDS:
            value = request.form.get(field)
            if value:
                setattr(inst, field, date.fromisoformat(value))
        log_action("institution_profile_updated", "Institution", inst.id,
                   old_value=old, new_value={f: getattr(inst, f) for f in PROFILE_FIELDS})
        db.session.commit()
        flash("Institution profile updated.", "success")
        return redirect(url_for("institution.profile"))
    return render_template("institution/profile.html", inst=inst)


@bp.route("/settings", methods=["GET", "POST"])
@permission_required(P_MANAGE_SETTINGS)
def settings():
    inst = current_user.institution
    if request.method == "POST":
        settings = inst.settings
        # Sensitive optional fields (ethnicity/religion/worship) policy
        enable = bool(request.form.get("sensitive_fields_enabled"))
        reason = request.form.get("sensitive_fields_reason") or ""
        if enable and not reason:
            flash("A reason must be recorded to enable sensitive optional fields.",
                  "danger")
            return redirect(url_for("institution.settings"))
        old = dict(settings)
        settings["sensitive_fields_enabled"] = enable
        settings["sensitive_fields_reason"] = reason
        # Provisioning rates
        rates = {}
        from mcag.constants import ARREARS_BUCKETS
        for key, _label, _lo, _hi in ARREARS_BUCKETS:
            value = request.form.get(f"prov_{key}")
            if value:
                rates[key] = value
        if rates:
            settings["provision_rates"] = rates
        settings["penalty_max_daily_percent"] = request.form.get(
            "penalty_max_daily_percent") or settings.get("penalty_max_daily_percent", "1")
        settings["mrt_region"] = request.form.get("mrt_region") or settings.get("mrt_region")
        inst.set_settings(settings)
        log_action("institution_settings_updated", "Institution", inst.id,
                   old_value=old, new_value=settings)
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("institution.settings"))
    return render_template("institution/settings.html", inst=inst)


# ---------------------------------------------------------------------------
# Staff users
# ---------------------------------------------------------------------------
@bp.route("/users")
@permission_required(P_MANAGE_USERS)
def users():
    records = tenant_query(User).order_by(User.full_name).all()
    return render_template("institution/users.html", users=records,
                           roles=INSTITUTION_ROLES)


@bp.route("/users/new", methods=["GET", "POST"])
@permission_required(P_MANAGE_USERS)
def user_new():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        role = request.form.get("role")
        password = request.form.get("password") or ""
        valid_roles = {r for r, _ in INSTITUTION_ROLES}
        if role not in valid_roles:
            flash("Invalid role.", "danger")
            return render_template("institution/user_form.html", roles=INSTITUTION_ROLES)
        if User.query.filter(db.func.lower(User.email) == email).first():
            flash("A user with that email already exists.", "danger")
            return render_template("institution/user_form.html", roles=INSTITUTION_ROLES)
        errors = password_strength_errors(password)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("institution/user_form.html", roles=INSTITUTION_ROLES)
        user = User(
            institution_id=current_user.institution_id,
            email=email,
            full_name=request.form.get("full_name") or email,
            phone=request.form.get("phone"),
            role=role,
            must_change_password=True,
            approval_limit=request.form.get("approval_limit") or None,
        )
        user.set_password(password)
        db.session.add(user)
        log_action("user_created", "User", None,
                   new_value={"email": email, "role": role})
        db.session.commit()
        flash("Staff user created. They must change the password at first login.",
              "success")
        return redirect(url_for("institution.users"))
    return render_template("institution/user_form.html", roles=INSTITUTION_ROLES)


@bp.route("/users/<int:user_id>", methods=["GET", "POST"])
@permission_required(P_MANAGE_USERS)
def user_detail(user_id):
    user = db.session.get(User, user_id)
    if user is None or user.institution_id != current_user.institution_id:
        from flask import abort
        abort(404)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "deactivate":
            if user.id == current_user.id:
                flash("You cannot deactivate your own account.", "danger")
            else:
                user.is_active_user = False
                log_action("user_deactivated", "User", user.id)
                flash("User deactivated.", "success")
        elif action == "activate":
            user.is_active_user = True
            log_action("user_activated", "User", user.id)
            flash("User reactivated.", "success")
        elif action == "update":
            old_role = user.role
            role = request.form.get("role")
            if role in {r for r, _ in INSTITUTION_ROLES}:
                user.role = role
            user.full_name = request.form.get("full_name") or user.full_name
            user.phone = request.form.get("phone")
            user.approval_limit = request.form.get("approval_limit") or None
            log_action("user_permissions_changed", "User", user.id,
                       old_value={"role": old_role}, new_value={"role": user.role})
            flash("User updated.", "success")
        elif action == "reset_password":
            password = request.form.get("password") or ""
            errors = password_strength_errors(password)
            if errors:
                for e in errors:
                    flash(e, "danger")
                return redirect(url_for("institution.user_detail", user_id=user.id))
            user.set_password(password)
            user.must_change_password = True
            log_action("user_password_admin_reset", "User", user.id)
            flash("Password reset. The user must change it at next login.", "success")
        db.session.commit()
        return redirect(url_for("institution.user_detail", user_id=user.id))
    history = (LoginEvent.query.filter_by(user_id=user.id)
               .order_by(LoginEvent.occurred_at.desc()).limit(30).all())
    return render_template("institution/user_detail.html", user=user,
                           roles=INSTITUTION_ROLES, history=history)


# ---------------------------------------------------------------------------
# Collection zones (field operational areas — NOT branches)
# ---------------------------------------------------------------------------
@bp.route("/zones", methods=["GET", "POST"])
@permission_required(P_VIEW)
def zones():
    if request.method == "POST":
        if not current_user.can(P_MANAGE_SETTINGS):
            flash("You do not have permission to manage collection zones.", "danger")
            return redirect(url_for("institution.zones"))
        zone = CollectionZone(
            name=(request.form.get("name") or "").strip(),
            zone_type=request.form.get("zone_type") or "zone",
            description=request.form.get("description"),
            assigned_officer_id=request.form.get("assigned_officer_id", type=int) or None,
        )
        if not zone.name:
            flash("Zone name is required.", "danger")
            return redirect(url_for("institution.zones"))
        stamp_tenant(zone)
        db.session.add(zone)
        log_action("collection_zone_created", "CollectionZone", None,
                   new_value={"name": zone.name})
        db.session.commit()
        flash("Collection zone created. Note: a collection zone is a field "
              "operational area, not a branch or separate business office.",
              "success")
        return redirect(url_for("institution.zones"))
    records = tenant_query(CollectionZone).order_by(CollectionZone.name).all()
    officers = tenant_query(User).filter(User.is_active_user.is_(True)).all()
    return render_template("institution/zones.html", zones=records, officers=officers)


@bp.route("/zones/<int:zone_id>/toggle", methods=["POST"])
@permission_required(P_MANAGE_SETTINGS)
def zone_toggle(zone_id):
    zone = get_tenant_or_404(CollectionZone, zone_id)
    zone.active = not zone.active
    db.session.commit()
    flash("Zone updated.", "success")
    return redirect(url_for("institution.zones"))


@bp.route("/support", methods=["GET", "POST"])
@permission_required(P_VIEW)
def support():
    if request.method == "POST":
        record = SupportRequest(
            institution_id=current_user.institution_id,
            raised_by_id=current_user.id,
            subject=request.form.get("subject") or "Support request",
            message=request.form.get("message") or "",
        )
        db.session.add(record)
        db.session.commit()
        flash("Support request submitted to the platform team.", "success")
        return redirect(url_for("institution.support"))
    records = (SupportRequest.query
               .filter_by(institution_id=current_user.institution_id)
               .order_by(SupportRequest.created_at.desc()).all())
    return render_template("institution/support.html", requests=records)
