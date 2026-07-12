"""Arrears classification, recovery actions, penalties, demand letters."""
from datetime import date

from flask import (
    Blueprint, Response, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import P_EDIT, P_RECOVERY, P_VIEW
from mcag.extensions import db
from mcag.models import Loan, RecoveryAction, ScheduleInstalment
from mcag.services.arrears import classify_portfolio
from mcag.services.audit import log_action
from mcag.services.loan_service import (
    LoanServiceError, apply_penalty, record_recovery_after_write_off,
)
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D

bp = Blueprint("arrears", __name__)

RECOVERY_ACTIONS = [
    ("call", "Telephone call"), ("visit", "Customer visit"),
    ("guarantor_contact", "Guarantor contact"), ("promise_to_pay", "Promise to pay"),
    ("demand_letter", "Demand letter"), ("final_demand", "Final demand"),
    ("legal_referral", "Legal referral"), ("collateral_action", "Collateral action"),
]

LETTER_TEMPLATES = {
    "reminder": ("pdf/letter_reminder.html", "Payment reminder"),
    "first_demand": ("pdf/letter_first_demand.html", "First demand letter"),
    "final_demand": ("pdf/letter_final_demand.html", "Final demand letter"),
    "guarantor_demand": ("pdf/letter_guarantor_demand.html", "Guarantor demand"),
    "balance_statement": ("pdf/letter_balance.html", "Statement of outstanding balance"),
}


@bp.route("/")
@permission_required(P_VIEW)
def index():
    portfolio = classify_portfolio(current_user.institution, date.today())
    return render_template("arrears/index.html", portfolio=portfolio)


@bp.route("/recovery")
@permission_required(P_VIEW)
def recovery():
    actions = (tenant_query(RecoveryAction)
               .order_by(RecoveryAction.action_date.desc()).limit(100).all())
    overdue_loans = [d for d in classify_portfolio(
        current_user.institution, date.today())["detail"] if d["days_overdue"] > 0]
    return render_template("arrears/recovery.html", actions=actions,
                           overdue=overdue_loans, action_types=RECOVERY_ACTIONS)


@bp.route("/recovery/record", methods=["POST"])
@permission_required(P_RECOVERY)
def record_action():
    loan = get_tenant_or_404(Loan, request.form.get("loan_id", type=int))
    action = RecoveryAction(
        loan_id=loan.id,
        action_type=request.form.get("action_type") or "call",
        action_date=date.fromisoformat(request.form.get("action_date")
                                       or date.today().isoformat()),
        officer_id=current_user.id,
        notes=request.form.get("notes"),
        promised_amount=request.form.get("promised_amount") or None,
        promise_date=(date.fromisoformat(request.form["promise_date"])
                      if request.form.get("promise_date") else None),
        outcome=request.form.get("outcome"),
    )
    stamp_tenant(action)
    db.session.add(action)
    log_action("recovery_action_recorded", "RecoveryAction", None,
               new_value={"loan": loan.loan_number, "type": action.action_type})
    db.session.commit()
    flash("Recovery action recorded.", "success")
    return redirect(url_for("arrears.recovery"))


@bp.route("/loans/<int:loan_id>/letters/<letter>.pdf")
@permission_required(P_VIEW)
def letter_pdf(loan_id, letter):
    loan = get_tenant_or_404(Loan, loan_id)
    if letter not in LETTER_TEMPLATES:
        from flask import abort
        abort(404)
    template, title = LETTER_TEMPLATES[letter]
    from mcag.services.pdf import render_pdf
    pdf = render_pdf(template, loan=loan, institution=current_user.institution,
                     today=date.today(), title=title)
    log_action("document_download", "Loan", loan.id, new_value={"file": title})
    db.session.commit()
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition":
            f"attachment; filename={letter}-{loan.loan_number}.pdf"})


@bp.route("/loans/<int:loan_id>/penalty", methods=["POST"])
@permission_required(P_EDIT)
def penalty(loan_id):
    loan = get_tenant_or_404(Loan, loan_id)
    inst_no = request.form.get("instalment_number", type=int)
    inst = next((i for i in loan.instalments if i.number == inst_no), None)
    if inst is None:
        flash("Instalment not found.", "danger")
        return redirect(url_for("loans.detail", loan_id=loan.id))
    product = loan.product
    days = inst.days_overdue(date.today())
    if days <= (product.penalty_grace_days or 0):
        flash("Instalment is within the penalty grace period.", "danger")
        return redirect(url_for("loans.detail", loan_id=loan.id))
    # Penalty computed from product configuration — never a hard-coded rate.
    base = D(inst.total_due) - D(inst.principal_paid) - D(inst.interest_paid) - D(inst.fees_paid)
    if product.penalty_basis == "overdue_principal":
        base = D(loan.principal_outstanding)
    amount = D(product.penalty_fixed_amount or 0) + (
        base * D(product.penalty_rate_percent or 0) / 100)
    if product.penalty_max_percent:
        cap = D(inst.total_due) * D(product.penalty_max_percent) / 100
        existing = D(inst.penalties_due)
        amount = min(amount, max(cap - existing, D(0)))
    try:
        if amount <= 0:
            flash("No penalty applicable (cap reached or zero configuration).",
                  "warning")
        else:
            apply_penalty(inst, amount,
                          f"Penalty per product {product.code} configuration",
                          current_user.institution, current_user)
            db.session.commit()
            flash("Penalty applied per product configuration.", "success")
    except LoanServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("loans.detail", loan_id=loan.id))


@bp.route("/loans/<int:loan_id>/recovery-payment", methods=["POST"])
@permission_required(P_RECOVERY)
def recovery_payment(loan_id):
    """Cash recovered on a written-off loan."""
    loan = get_tenant_or_404(Loan, loan_id)
    try:
        record_recovery_after_write_off(
            loan, request.form.get("amount"),
            date.fromisoformat(request.form.get("paid_at") or date.today().isoformat()),
            request.form.get("method") or "cash",
            current_user.institution, current_user)
        db.session.commit()
        flash("Recovery recorded.", "success")
    except LoanServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("loans.detail", loan_id=loan.id))
