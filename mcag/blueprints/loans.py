"""Loans: disbursement (maker-checker), schedule, ledger, statements,
early settlement, waivers, restructuring, write-off."""
import json
from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template, request,
    url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import page_args, permission_required
from mcag.constants import (
    APP_DISBURSED, APP_READY_DISBURSE, DISBURSEMENT_METHODS, LOAN_ACTIVE,
    P_APPROVE, P_DISBURSE, P_EDIT, P_RESTRUCTURE, P_VIEW, P_WAIVE_CHARGES,
    P_WRITE_OFF,
)
from mcag.extensions import db
from mcag.models import (
    Disbursement, Loan, LoanApplication, LoanRestructure, OfferLetter,
    SettlementQuote, Waiver,
)
from mcag.models.base import utcnow
from mcag.services.audit import log_action
from mcag.services.loan_engine import early_settlement_quote
from mcag.services.loan_service import (
    LoanServiceError, complete_disbursement, create_loan_from_application,
    disbursement_checklist, write_off_loan,
)
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D, money

bp = Blueprint("loans", __name__)


@bp.route("/")
@permission_required(P_VIEW)
def index():
    page, per_page = page_args()
    status = request.args.get("status")
    query = tenant_query(Loan)
    if status:
        query = query.filter(Loan.status == status)
    pagination = query.order_by(Loan.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return render_template("loans/index.html", pagination=pagination, status=status)


@bp.route("/<int:loan_id>")
@permission_required(P_VIEW)
def detail(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    return render_template("loans/detail.html", loan=loan)


@bp.route("/<int:loan_id>/ledger")
@permission_required(P_VIEW)
def ledger(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    entries = loan.ledger_entries.all()
    return render_template("loans/ledger.html", loan=loan, entries=entries)


@bp.route("/<int:loan_id>/statement.pdf")
@permission_required(P_VIEW)
def statement_pdf(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    from mcag.services.pdf import render_pdf
    pdf = render_pdf("pdf/loan_statement.html", loan=loan,
                     entries=loan.ledger_entries.all(),
                     institution=current_user.institution, today=date.today())
    log_action("document_download", "Loan", loan.id,
               new_value={"file": "loan statement"})
    db.session.commit()
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=statement-{loan.loan_number}.pdf"})


# ---------------------------------------------------------------------------
# Disbursement — initiate (Accounts Officer) then authorise (Manager)
# ---------------------------------------------------------------------------
@bp.route("/disbursements")
@permission_required(P_VIEW)
def disbursements():
    records = tenant_query(Disbursement).order_by(Disbursement.created_at.desc()).all()
    ready_apps = tenant_query(LoanApplication).filter(
        LoanApplication.status.in_(
            ["Offer Accepted", "Documentation Completed", APP_READY_DISBURSE])).all()
    return render_template("loans/disbursements.html", records=records,
                           ready_apps=ready_apps, methods=DISBURSEMENT_METHODS)


@bp.route("/disbursements/initiate/<int:app_id>", methods=["GET", "POST"])
@permission_required(P_DISBURSE)
def initiate_disbursement(app_id):
    application = get_tenant_or_404(LoanApplication, app_id)
    checklist = disbursement_checklist(application)
    failed = [label for label, ok in checklist if not ok]
    if request.method == "POST":
        if failed:
            flash("Pre-disbursement checks failed: " + "; ".join(failed), "danger")
            return redirect(url_for("loans.initiate_disbursement", app_id=app_id))
        offer = next((o for o in application.offers if o.status == "accepted"), None)
        calc = json.loads(offer.calculation_json)
        from decimal import Decimal
        principal = D(calc["principal"])
        total_fees = D(calc["total_fees"])
        fees_deducted = total_fees if calc.get("fees_deducted_upfront") else Decimal("0")
        fees_separate = Decimal("0") if calc.get("fees_deducted_upfront") else total_fees
        net = money(principal - fees_deducted)

        # Rebuild the locked schedule anchored to the actual disbursement date
        disb_date = date.fromisoformat(
            request.form.get("disbursement_date") or date.today().isoformat())
        from mcag.blueprints.applications import _build_offer_calc
        calc_live = _build_offer_calc(application, disbursement_date=disb_date)

        loan = create_loan_from_application(
            application, calc_live, current_user.institution, current_user)
        disbursement = Disbursement(
            loan_id=loan.id,
            gross_principal=principal,
            fees_deducted=fees_deducted,
            fees_paid_separately=fees_separate,
            net_amount=net,
            disbursement_date=disb_date,
            method=request.form.get("method") or "cash",
            bank_account=request.form.get("bank_account"),
            momo_number=request.form.get("momo_number"),
            cheque_details=request.form.get("cheque_details"),
            payment_reference=request.form.get("payment_reference"),
            initiated_by_id=current_user.id,
            status="pending",
        )
        stamp_tenant(disbursement)
        db.session.add(disbursement)
        application.set_status(APP_READY_DISBURSE, current_user.id,
                               "Disbursement initiated — awaiting authorisation")
        log_action("disbursement_initiated", "Disbursement", None,
                   new_value={"loan": loan.loan_number, "net": str(net)})
        db.session.commit()
        flash("Disbursement initiated. A different authorised officer must "
              "authorise it (maker-checker).", "success")
        return redirect(url_for("loans.disbursements"))
    return render_template("loans/initiate_disbursement.html", app=application,
                           checklist=checklist, methods=DISBURSEMENT_METHODS)


@bp.route("/disbursements/<int:disb_id>/authorise", methods=["POST"])
@permission_required(P_APPROVE)
def authorise_disbursement(disb_id):
    disbursement = get_tenant_or_404(Disbursement, disb_id)
    if disbursement.status != "pending":
        flash("Disbursement is not pending authorisation.", "danger")
        return redirect(url_for("loans.disbursements"))
    if disbursement.initiated_by_id == current_user.id:
        flash("Maker-checker control: the initiating officer cannot authorise "
              "the same disbursement.", "danger")
        return redirect(url_for("loans.disbursements"))
    loan = disbursement.loan
    try:
        disbursement.authorised_by_id = current_user.id
        disbursement.authorised_at = utcnow()
        complete_disbursement(loan, disbursement, current_user.institution, current_user)
        loan.application.set_status(APP_DISBURSED, current_user.id, "Disbursed")
        from mcag.services.alerts import scan_disbursement_timing
        scan_disbursement_timing(loan)
        db.session.commit()
        flash(f"Loan {loan.loan_number} disbursed.", "success")
    except LoanServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("loans.detail", loan_id=loan.id))


# ---------------------------------------------------------------------------
# Early settlement
# ---------------------------------------------------------------------------
@bp.route("/<int:loan_id>/settlement", methods=["GET", "POST"])
@permission_required(P_VIEW)
def settlement(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    if request.method == "POST":
        if not current_user.can(P_EDIT):
            abort(403)
        quote_data = early_settlement_quote(
            loan, date.today(),
            loan.product.early_settlement_charge_percent)
        quote = SettlementQuote(
            loan_id=loan.id,
            quote_date=date.today(),
            valid_until=date.today() + timedelta(days=14),
            generated_by_id=current_user.id,
            **{k: v for k, v in quote_data.items()},
        )
        stamp_tenant(quote)
        db.session.add(quote)
        log_action("settlement_quote_generated", "SettlementQuote", None,
                   new_value={"loan": loan.loan_number,
                              "total": str(quote_data["total_settlement"])})
        db.session.commit()
        flash("Settlement quotation generated.", "success")
        return redirect(url_for("loans.settlement", loan_id=loan.id))
    quotes = tenant_query(SettlementQuote).filter_by(loan_id=loan.id).order_by(
        SettlementQuote.created_at.desc()).all()
    return render_template("loans/settlement.html", loan=loan, quotes=quotes)


@bp.route("/<int:loan_id>/closure-pack.pdf")
@permission_required(P_VIEW)
def closure_pack(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    if loan.status != "Closed":
        flash("Closure documents are only available for closed loans.", "danger")
        return redirect(url_for("loans.detail", loan_id=loan.id))
    from mcag.services.pdf import render_pdf
    pdf = render_pdf("pdf/loan_closure.html", loan=loan,
                     institution=current_user.institution, today=date.today())
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=closure-{loan.loan_number}.pdf"})


# ---------------------------------------------------------------------------
# Waivers (maker-checker)
# ---------------------------------------------------------------------------
@bp.route("/<int:loan_id>/waivers", methods=["POST"])
@permission_required(P_WAIVE_CHARGES)
def request_waiver(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    waiver = Waiver(
        loan_id=loan.id,
        waiver_type=request.form.get("waiver_type") or "penalty",
        amount=D(request.form.get("amount") or 0),
        reason=request.form.get("reason") or "",
        requested_by_id=current_user.id,
    )
    if D(waiver.amount) <= 0 or not waiver.reason:
        flash("Waiver amount and reason are required.", "danger")
        return redirect(url_for("loans.detail", loan_id=loan.id))
    stamp_tenant(waiver)
    db.session.add(waiver)
    log_action("waiver_requested", "Waiver", None,
               new_value={"loan": loan.loan_number, "amount": str(waiver.amount)})
    db.session.commit()
    flash("Waiver requested — awaiting approval by a different officer.", "success")
    return redirect(url_for("loans.detail", loan_id=loan.id))


@bp.route("/waivers/<int:waiver_id>/approve", methods=["POST"])
@permission_required(P_WAIVE_CHARGES)
def approve_waiver_route(waiver_id):
    waiver = get_tenant_or_404(Waiver, waiver_id)
    from mcag.services.loan_service import approve_waiver
    try:
        if request.form.get("decision") == "reject":
            waiver.status = "rejected"
            waiver.approved_by_id = current_user.id
            log_action("waiver_rejected", "Waiver", waiver.id)
        else:
            approve_waiver(waiver, current_user, current_user.institution)
        db.session.commit()
        flash("Waiver decision recorded.", "success")
    except LoanServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("loans.detail", loan_id=waiver.loan_id))


# ---------------------------------------------------------------------------
# Write-off (maker-checker)
# ---------------------------------------------------------------------------
@bp.route("/<int:loan_id>/write-off", methods=["POST"])
@permission_required(P_WRITE_OFF)
def write_off(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    action = request.form.get("action")
    if action == "request":
        loan.write_off_requested_by_id = current_user.id
        loan.write_off_reason = request.form.get("reason") or ""
        log_action("write_off_requested", "Loan", loan.id,
                   new_value={"reason": loan.write_off_reason})
        db.session.commit()
        flash("Write-off requested. A different authorised officer must "
              "approve it.", "success")
    elif action == "approve":
        if loan.write_off_requested_by_id is None:
            flash("No write-off request pending.", "danger")
            return redirect(url_for("loans.detail", loan_id=loan.id))
        try:
            requester = db.session.get(
                type(current_user), loan.write_off_requested_by_id)
            write_off_loan(loan, loan.write_off_reason or "", requester,
                           current_user, current_user.institution)
            db.session.commit()
            flash("Loan written off.", "success")
        except LoanServiceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return redirect(url_for("loans.detail", loan_id=loan.id))


# ---------------------------------------------------------------------------
# Restructuring
# ---------------------------------------------------------------------------
@bp.route("/<int:loan_id>/restructure", methods=["GET", "POST"])
@permission_required(P_RESTRUCTURE)
def restructure(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    if request.method == "POST":
        record = LoanRestructure(
            original_loan_id=loan.id,
            restructure_type=request.form.get("restructure_type") or "reschedule",
            reason=request.form.get("reason") or "",
            customer_request_reference=request.form.get("customer_request_reference"),
            old_balance=loan.total_outstanding,
            new_terms=request.form.get("new_terms"),
            requested_by_id=current_user.id,
            credit_bureau_treatment=request.form.get("credit_bureau_treatment"),
        )
        if not record.reason:
            flash("A reason is required for restructuring.", "danger")
            return redirect(url_for("loans.restructure", loan_id=loan.id))
        stamp_tenant(record)
        db.session.add(record)
        log_action("restructure_requested", "LoanRestructure", None,
                   new_value={"loan": loan.loan_number,
                              "type": record.restructure_type})
        db.session.commit()
        flash("Restructure request recorded — requires approval.", "success")
        return redirect(url_for("loans.restructure", loan_id=loan.id))
    requests_ = tenant_query(LoanRestructure).filter_by(
        original_loan_id=loan.id).order_by(LoanRestructure.created_at.desc()).all()
    return render_template("loans/restructure.html", loan=loan, requests=requests_)


@bp.route("/restructures/<int:rec_id>/approve", methods=["POST"])
@permission_required(P_APPROVE)
def approve_restructure(rec_id):
    record = get_tenant_or_404(LoanRestructure, rec_id)
    if record.requested_by_id == current_user.id:
        flash("Maker-checker control: you cannot approve your own restructure "
              "request.", "danger")
        return redirect(url_for("loans.restructure", loan_id=record.original_loan_id))
    if record.status != "pending":
        flash("Request is not pending.", "danger")
        return redirect(url_for("loans.restructure", loan_id=record.original_loan_id))
    decision = request.form.get("decision")
    if decision == "reject":
        record.status = "rejected"
    else:
        record.status = "approved"
        record.approved_by_id = current_user.id
        record.approved_at = utcnow()
        loan = record.original_loan
        loan.status = "Restructured"
        # The original loan stays visible; new terms are captured on the
        # linked application/loan created via the normal workflow.
    log_action("restructure_decision", "LoanRestructure", record.id,
               new_value=record.status)
    db.session.commit()
    flash("Restructure decision recorded.", "success")
    return redirect(url_for("loans.restructure", loan_id=record.original_loan_id))
