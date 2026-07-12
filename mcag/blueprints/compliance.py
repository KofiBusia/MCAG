"""Compliance: complaints, credit bureau register, fraud alerts review,
data protection records, audit logs."""
import csv
import io
from datetime import date

from flask import (
    Blueprint, Response, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import page_args, permission_required
from mcag.constants import (
    COMPLAINT_CHANNELS, P_COMPLIANCE, P_EDIT, P_VIEW, P_VIEW_AUDIT,
)
from mcag.extensions import db
from mcag.models import (
    AuditLog, Complaint, ConsentRecord, CreditBureauEnquiry,
    CreditBureauSubmission, Customer, DataBreachRecord, DataRequest,
    DuplicateAlert, Loan,
)
from mcag.models.base import utcnow
from mcag.services.audit import log_action
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query

bp = Blueprint("compliance", __name__)


# ---------------------------------------------------------------------------
# Complaints register
# ---------------------------------------------------------------------------
@bp.route("/complaints", methods=["GET", "POST"])
@permission_required(P_VIEW)
def complaints():
    if request.method == "POST":
        inst = current_user.institution
        seq = inst.take_sequence("next_complaint_seq")
        record = Complaint(
            complaint_number=f"CMP-{seq:06d}",
            customer_id=request.form.get("customer_id", type=int) or None,
            complainant_name=request.form.get("complainant_name"),
            date_received=date.fromisoformat(request.form.get("date_received")
                                             or date.today().isoformat()),
            channel=request.form.get("channel") or "office_visit",
            category=request.form.get("category") or "General",
            description=request.form.get("description") or "",
            assigned_officer_id=request.form.get("assigned_officer_id", type=int) or None,
        )
        stamp_tenant(record)
        db.session.add(record)
        log_action("complaint_recorded", "Complaint", None,
                   new_value={"number": record.complaint_number})
        db.session.commit()
        flash(f"Complaint {record.complaint_number} recorded.", "success")
        return redirect(url_for("compliance.complaints"))
    records = tenant_query(Complaint).order_by(Complaint.created_at.desc()).all()
    customers = tenant_query(Customer).order_by(Customer.full_name).all()
    from mcag.models import User
    officers = tenant_query(User).filter_by(is_active_user=True).all()
    return render_template("compliance/complaints.html", complaints=records,
                           channels=COMPLAINT_CHANNELS, customers=customers,
                           officers=officers)


@bp.route("/complaints/<int:complaint_id>", methods=["GET", "POST"])
@permission_required(P_VIEW)
def complaint_detail(complaint_id):
    record = get_tenant_or_404(Complaint, complaint_id)
    if request.method == "POST":
        if not current_user.can(P_EDIT):
            flash("No permission to update complaints.", "danger")
            return redirect(url_for("compliance.complaint_detail",
                                    complaint_id=record.id))
        record.investigation_notes = request.form.get("investigation_notes")
        record.resolution = request.form.get("resolution")
        record.status = request.form.get("status") or record.status
        record.customer_informed = bool(request.form.get("customer_informed"))
        record.escalated = record.status == "escalated"
        record.escalation_details = request.form.get("escalation_details")
        if record.status == "resolved" and not record.date_resolved:
            record.date_resolved = date.today()
        log_action("complaint_updated", "Complaint", record.id,
                   new_value={"status": record.status})
        db.session.commit()
        flash("Complaint updated.", "success")
        return redirect(url_for("compliance.complaint_detail", complaint_id=record.id))
    return render_template("compliance/complaint_detail.html", c=record)


# ---------------------------------------------------------------------------
# Credit bureau register
# ---------------------------------------------------------------------------
@bp.route("/credit-bureau", methods=["GET", "POST"])
@permission_required(P_VIEW)
def credit_bureau():
    if request.method == "POST":
        if not current_user.can(P_EDIT):
            flash("No permission.", "danger")
            return redirect(url_for("compliance.credit_bureau"))
        customer = get_tenant_or_404(Customer, request.form.get("customer_id", type=int))
        record = CreditBureauEnquiry(
            customer_id=customer.id,
            application_id=request.form.get("application_id", type=int) or None,
            consent_given=bool(request.form.get("consent_given")),
            consent_date=(date.fromisoformat(request.form["consent_date"])
                          if request.form.get("consent_date") else None),
            bureau_name=request.form.get("bureau_name") or "",
            enquiry_date=date.fromisoformat(request.form.get("enquiry_date")
                                            or date.today().isoformat()),
            report_reference=request.form.get("report_reference"),
            existing_facilities=request.form.get("existing_facilities"),
            outstanding_balances=request.form.get("outstanding_balances") or None,
            arrears=request.form.get("arrears") or None,
            defaults_found=bool(request.form.get("defaults_found")),
            officer_comments=request.form.get("officer_comments"),
            impact_on_decision=request.form.get("impact_on_decision"),
            officer_id=current_user.id,
        )
        if not record.consent_given:
            flash("Customer consent must be recorded before a bureau enquiry.",
                  "danger")
            return redirect(url_for("compliance.credit_bureau"))
        stamp_tenant(record)
        db.session.add(record)
        db.session.add(stamp_tenant(ConsentRecord(
            customer_id=customer.id, consent_type="credit_bureau",
            recorded_by_id=current_user.id)))
        log_action("bureau_enquiry_recorded", "CreditBureauEnquiry", None,
                   new_value={"customer": customer.customer_number,
                              "bureau": record.bureau_name})
        db.session.commit()
        flash("Credit bureau enquiry recorded.", "success")
        return redirect(url_for("compliance.credit_bureau"))
    records = tenant_query(CreditBureauEnquiry).order_by(
        CreditBureauEnquiry.enquiry_date.desc()).all()
    submissions = tenant_query(CreditBureauSubmission).order_by(
        CreditBureauSubmission.period.desc()).all()
    customers = tenant_query(Customer).order_by(Customer.full_name).all()
    return render_template("compliance/credit_bureau.html", enquiries=records,
                           submissions=submissions, customers=customers)


@bp.route("/credit-bureau/export.csv")
@permission_required(P_COMPLIANCE)
def bureau_export():
    """CSV export of active facilities for bureau submission. A live bureau
    integration can replace this later — no fake integration is included."""
    period = request.args.get("period") or date.today().strftime("%Y-%m")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["customer_number", "full_name", "ghana_card", "loan_number",
                     "principal", "disbursement_date", "principal_outstanding",
                     "interest_outstanding", "days_overdue", "status"])
    loans = tenant_query(Loan).all()
    for loan in loans:
        c = loan.customer
        writer.writerow([
            c.customer_number, c.full_name, c.ghana_card_number or "",
            loan.loan_number, loan.principal,
            loan.disbursed_at.date() if loan.disbursed_at else "",
            loan.principal_outstanding, loan.interest_outstanding,
            loan.days_overdue(), loan.status])
    submission = CreditBureauSubmission(
        bureau_name=request.args.get("bureau") or "Export",
        period=period, submitted_at=utcnow(),
        submitted_by_id=current_user.id, record_count=len(loans),
        status="exported")
    stamp_tenant(submission)
    db.session.add(submission)
    log_action("data_export", "CreditBureauSubmission", None,
               new_value={"period": period, "records": len(loans)})
    db.session.commit()
    return Response(output.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f"attachment; filename=bureau-export-{period}.csv"})


# ---------------------------------------------------------------------------
# Fraud / duplicate alerts review
# ---------------------------------------------------------------------------
@bp.route("/alerts")
@permission_required(P_VIEW)
def alerts():
    status = request.args.get("status", "open")
    query = tenant_query(DuplicateAlert)
    if status:
        query = query.filter(DuplicateAlert.status == status)
    records = query.order_by(DuplicateAlert.created_at.desc()).all()
    return render_template("compliance/alerts.html", alerts=records, status=status)


@bp.route("/alerts/<int:alert_id>/review", methods=["POST"])
@permission_required(P_COMPLIANCE)
def alert_review(alert_id):
    alert = get_tenant_or_404(DuplicateAlert, alert_id)
    decision = request.form.get("decision")
    if decision not in ("cleared", "confirmed", "under_review"):
        flash("Unknown decision.", "danger")
        return redirect(url_for("compliance.alerts"))
    alert.status = decision
    alert.reviewed_by_id = current_user.id
    alert.reviewed_at = utcnow()
    alert.review_notes = request.form.get("notes")
    log_action("alert_reviewed", "DuplicateAlert", alert.id,
               new_value={"decision": decision})
    db.session.commit()
    flash("Alert reviewed.", "success")
    return redirect(url_for("compliance.alerts"))


# ---------------------------------------------------------------------------
# Data protection
# ---------------------------------------------------------------------------
@bp.route("/data-protection", methods=["GET", "POST"])
@permission_required(P_VIEW)
def data_protection():
    customers = tenant_query(Customer).order_by(Customer.full_name).all()
    if request.method == "POST":
        if not current_user.can(P_COMPLIANCE):
            flash("No permission.", "danger")
            return redirect(url_for("compliance.data_protection"))
        kind = request.form.get("record_kind")
        if kind == "request":
            record = DataRequest(
                customer_id=request.form.get("customer_id", type=int) or None,
                request_type=request.form.get("request_type") or "access",
                details=request.form.get("details") or "",
                handled_by_id=current_user.id,
            )
            stamp_tenant(record)
            db.session.add(record)
            log_action("data_request_recorded", "DataRequest", None)
        elif kind == "breach":
            record = DataBreachRecord(
                occurred_on=date.fromisoformat(request.form.get("occurred_on")
                                               or date.today().isoformat()),
                discovered_on=date.fromisoformat(request.form.get("discovered_on")
                                                 or date.today().isoformat()),
                description=request.form.get("details") or "",
                data_affected=request.form.get("data_affected"),
                persons_affected=request.form.get("persons_affected", type=int),
                containment_actions=request.form.get("containment_actions"),
                reported_to_dpc=bool(request.form.get("reported_to_dpc")),
                recorded_by_id=current_user.id,
            )
            stamp_tenant(record)
            db.session.add(record)
            log_action("data_breach_recorded", "DataBreachRecord", None)
        db.session.commit()
        flash("Record saved.", "success")
        return redirect(url_for("compliance.data_protection"))
    requests_ = tenant_query(DataRequest).order_by(DataRequest.created_at.desc()).all()
    breaches = tenant_query(DataBreachRecord).order_by(
        DataBreachRecord.created_at.desc()).all()
    consents = tenant_query(ConsentRecord).order_by(
        ConsentRecord.created_at.desc()).limit(100).all()
    return render_template("compliance/data_protection.html",
                           requests=requests_, breaches=breaches,
                           consents=consents, customers=customers)


# ---------------------------------------------------------------------------
# Audit logs (read-only; no delete routes exist anywhere)
# ---------------------------------------------------------------------------
@bp.route("/audit-logs")
@permission_required(P_VIEW_AUDIT)
def audit_logs():
    page, per_page = page_args(50)
    query = AuditLog.query.filter(
        AuditLog.institution_id == current_user.institution_id)
    action = request.args.get("action")
    if action:
        query = query.filter(AuditLog.action == action)
    pagination = query.order_by(AuditLog.occurred_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return render_template("compliance/audit_logs.html", pagination=pagination,
                           action=action)
