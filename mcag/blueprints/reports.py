"""Reports: MCAG returns, staff performance, inspection mode, exports."""
import csv
import io
import json
from datetime import date

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template, request,
    url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import (
    LOAN_ACTIVE, P_COMPLIANCE, P_EXPORT, P_INSPECT, P_VIEW,
)
from mcag.extensions import db
from mcag.models import (
    Collateral, Complaint, Customer, Disbursement, FundingSource, Guarantor,
    Loan, McagReturn, Repayment, User,
)
from mcag.models.base import utcnow
from mcag.services.arrears import classify_portfolio
from mcag.services.audit import log_action
from mcag.services.mcag_report import (
    QUARTERS, build_return_data, export_to_excel, serialize_data,
)
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D, money

bp = Blueprint("reports", __name__)


# ---------------------------------------------------------------------------
# MCAG returns
# ---------------------------------------------------------------------------
@bp.route("/mcag-returns", methods=["GET", "POST"])
@permission_required(P_VIEW)
def mcag_returns():
    if request.method == "POST":
        if not current_user.can(P_COMPLIANCE):
            flash("You do not have permission to generate MCAG returns.", "danger")
            return redirect(url_for("reports.mcag_returns"))
        year = request.form.get("year", type=int) or date.today().year
        quarter = request.form.get("quarter") or "Q1"
        if quarter not in QUARTERS:
            flash("Invalid quarter.", "danger")
            return redirect(url_for("reports.mcag_returns"))
        period = f"{year}-{quarter}"
        record = tenant_query(McagReturn).filter_by(period=period).first()
        if record and record.status in ("locked", "submitted"):
            flash("This reporting period is locked. Unlock is not permitted "
                  "after submission.", "danger")
            return redirect(url_for("reports.mcag_returns"))
        data = build_return_data(current_user.institution, year, quarter)
        if record is None:
            record = McagReturn(period=period)
            stamp_tenant(record)
            db.session.add(record)
        record.data_json = serialize_data(data)
        record.validation_json = json.dumps(data["validation"])
        record.generated_at = utcnow()
        record.generated_by_id = current_user.id
        record.status = "draft"
        log_action("mcag_return_generated", "McagReturn", None,
                   new_value={"period": period,
                              "errors": len(data["validation"])})
        db.session.commit()
        if data["validation"]:
            flash(f"Return generated with {len(data['validation'])} validation "
                  "issue(s). Correct the source transactions and regenerate.",
                  "warning")
        else:
            flash("Return generated with no validation errors.", "success")
        return redirect(url_for("reports.mcag_return_detail", return_id=record.id))
    records = tenant_query(McagReturn).order_by(McagReturn.period.desc()).all()
    return render_template("reports/mcag_returns.html", returns=records,
                           quarters=list(QUARTERS), today=date.today())


@bp.route("/mcag-returns/<int:return_id>")
@permission_required(P_VIEW)
def mcag_return_detail(return_id):
    record = get_tenant_or_404(McagReturn, return_id)
    data = json.loads(record.data_json) if record.data_json else None
    validation = json.loads(record.validation_json or "[]")
    return render_template("reports/mcag_return_detail.html", r=record,
                           data=data, validation=validation)


@bp.route("/mcag-returns/<int:return_id>/export.xlsx")
@permission_required(P_EXPORT)
def mcag_return_export(return_id):
    record = get_tenant_or_404(McagReturn, return_id)
    if not record.data_json:
        abort(404)
    data = json.loads(record.data_json)
    xlsx = export_to_excel(data)
    log_action("data_export", "McagReturn", record.id,
               new_value={"period": record.period, "format": "xlsx"})
    db.session.commit()
    return Response(
        xlsx,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename=MCAG-MRT-{record.period}.xlsx"})


@bp.route("/mcag-returns/<int:return_id>/lock", methods=["POST"])
@permission_required(P_COMPLIANCE)
def mcag_return_lock(return_id):
    record = get_tenant_or_404(McagReturn, return_id)
    action = request.form.get("action")
    if action == "lock" and record.status == "draft":
        validation = json.loads(record.validation_json or "[]")
        if validation:
            flash("Cannot lock a return with validation errors.", "danger")
            return redirect(url_for("reports.mcag_return_detail", return_id=record.id))
        record.status = "locked"
        record.locked_at = utcnow()
        record.locked_by_id = current_user.id
        log_action("mcag_return_locked", "McagReturn", record.id)
        flash("Reporting period locked.", "success")
    elif action == "submit" and record.status == "locked":
        record.status = "submitted"
        record.submitted_at = utcnow()
        record.submitted_by_id = current_user.id
        file = request.files.get("proof")
        if file and file.filename:
            from mcag.services.documents import DocumentError, save_upload
            try:
                document = save_upload(file, current_user.institution_id,
                                       "Proof of MCAG Submission", current_user,
                                       immutable=True)
                record.proof_document_id = document.id
            except DocumentError as exc:
                flash(str(exc), "danger")
        log_action("mcag_return_submitted", "McagReturn", record.id)
        flash("Return marked as submitted to MCAG.", "success")
    db.session.commit()
    return redirect(url_for("reports.mcag_return_detail", return_id=record.id))


# ---------------------------------------------------------------------------
# Staff performance
# ---------------------------------------------------------------------------
@bp.route("/staff-performance")
@permission_required(P_VIEW)
def staff_performance():
    today = date.today()
    staff = tenant_query(User).filter_by(is_active_user=True).all()
    portfolio = classify_portfolio(current_user.institution, today)
    detail_by_officer = {}
    for item in portfolio["detail"]:
        officer_id = item["loan"].officer_id
        agg = detail_by_officer.setdefault(officer_id, {
            "portfolio": money(0), "par30": money(0), "par90": money(0),
            "delinquent": 0})
        agg["portfolio"] = money(agg["portfolio"] + item["principal_outstanding"])
        if item["days_overdue"] > 30:
            agg["par30"] = money(agg["par30"] + item["principal_outstanding"])
        if item["days_overdue"] > 90:
            agg["par90"] = money(agg["par90"] + item["principal_outstanding"])
        if item["days_overdue"] > 0:
            agg["delinquent"] += 1

    rows = []
    for user in staff:
        from mcag.models import FieldVerification, LoanApplication, RecoveryAction
        apps = tenant_query(LoanApplication).filter_by(created_by_id=user.id).count()
        visits = tenant_query(FieldVerification).filter_by(officer_id=user.id).count()
        approved = tenant_query(LoanApplication).filter_by(
            approved_by_id=user.id).with_entities(
            db.func.coalesce(db.func.sum(LoanApplication.approved_amount), 0)).scalar()
        disbursed = tenant_query(Loan).filter_by(officer_id=user.id).with_entities(
            db.func.coalesce(db.func.sum(Loan.principal), 0)).scalar()
        collected = tenant_query(Repayment).filter_by(collector_id=user.id).filter(
            Repayment.reversed.is_(False)).with_entities(
            db.func.coalesce(db.func.sum(Repayment.amount), 0)).scalar()
        reversals = tenant_query(Repayment).filter_by(collector_id=user.id).filter(
            Repayment.reversed.is_(True)).count()
        recovery_actions = tenant_query(RecoveryAction).filter_by(
            officer_id=user.id).count()
        agg = detail_by_officer.get(user.id, {})
        rows.append({
            "user": user, "applications": apps, "field_visits": visits,
            "approved": D(approved), "disbursed": D(disbursed),
            "collected": D(collected), "reversals": reversals,
            "recovery_actions": recovery_actions,
            "portfolio": agg.get("portfolio", money(0)),
            "par30": agg.get("par30", money(0)),
            "par90": agg.get("par90", money(0)),
            "delinquent": agg.get("delinquent", 0),
        })
    return render_template("reports/staff_performance.html", rows=rows)


# ---------------------------------------------------------------------------
# Inspection mode (read-only registers for regulators/auditors)
# ---------------------------------------------------------------------------
INSPECTION_REGISTERS = {
    "staff": ("Staff register", User),
    "customers": ("Customer register", Customer),
    "loans": ("Loan register", Loan),
    "disbursements": ("Disbursement register", Disbursement),
    "repayments": ("Repayment register", Repayment),
    "guarantors": ("Guarantor register", Guarantor),
    "collateral": ("Collateral register", Collateral),
    "complaints": ("Complaints register", Complaint),
    "funding": ("Funding register", FundingSource),
}


@bp.route("/inspection")
@permission_required(P_INSPECT)
def inspection():
    counts = {key: (tenant_query(model).count()
                    if key != "staff" else
                    tenant_query(User).count())
              for key, (_, model) in INSPECTION_REGISTERS.items()}
    return render_template("reports/inspection.html",
                           registers=INSPECTION_REGISTERS, counts=counts)


@bp.route("/inspection/<register>.csv")
@permission_required(P_INSPECT)
def inspection_export(register):
    if register not in INSPECTION_REGISTERS:
        abort(404)
    title, model = INSPECTION_REGISTERS[register]
    records = tenant_query(model).all()
    output = io.StringIO()
    writer = csv.writer(output)
    columns = [c.name for c in model.__table__.columns
               if c.name not in ("password_hash", "totp_secret")]
    writer.writerow(columns)
    for record in records:
        writer.writerow([getattr(record, col) for col in columns])
    log_action("data_export", model.__name__, None,
               new_value={"register": register, "records": len(records)})
    db.session.commit()
    return Response(output.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename={register}-register.csv"})


@bp.route("/arrears-register")
@permission_required(P_VIEW)
def arrears_register():
    portfolio = classify_portfolio(current_user.institution, date.today())
    return render_template("reports/arrears_register.html", portfolio=portfolio)
