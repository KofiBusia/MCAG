"""Repayment collection, receipts, reversals, field collections, cashbook."""
from datetime import date

from flask import (
    Blueprint, Response, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import page_args, permission_required
from mcag.constants import (
    LOAN_ACTIVE, P_APPROVE, P_RECEIVE_REPAYMENT, P_REVERSE_PAYMENT, P_VIEW,
    PAYMENT_METHODS,
)
from mcag.extensions import db
from mcag.models import (
    CashbookDay, CollectionZone, Expense, Loan, Repayment, ScheduleInstalment,
)
from mcag.models.base import utcnow
from mcag.services.audit import log_action
from mcag.services.loan_service import (
    LoanServiceError, record_repayment, reverse_repayment,
)
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D, money

bp = Blueprint("collections", __name__)


@bp.route("/", methods=["GET", "POST"])
@permission_required(P_RECEIVE_REPAYMENT)
def index():
    if request.method == "POST":
        loan = get_tenant_or_404(Loan, request.form.get("loan_id", type=int))
        try:
            repayment = record_repayment(
                loan,
                amount=request.form.get("amount"),
                paid_at=date.fromisoformat(request.form.get("paid_at")
                                           or date.today().isoformat()),
                method=request.form.get("method") or "cash",
                institution=current_user.institution,
                user=current_user,
                external_reference=request.form.get("external_reference") or "",
                zone_id=request.form.get("collection_zone_id", type=int),
            )
            if repayment.paid_at < date.today():
                from mcag.services.alerts import _raise_alert
                _raise_alert(current_user.institution_id, "backdated_transaction",
                             f"Backdated repayment {repayment.receipt_number} "
                             f"dated {repayment.paid_at}.",
                             loan.customer, "Repayment", repayment.id)
            db.session.commit()
            flash(f"Receipt {repayment.receipt_number} issued.", "success")
            return redirect(url_for("collections.receipt", repayment_id=repayment.id))
        except LoanServiceError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    active_loans = (tenant_query(Loan)
                    .filter(Loan.status.in_([LOAN_ACTIVE, "Restructured"]))
                    .order_by(Loan.loan_number).all())
    zones = tenant_query(CollectionZone).filter_by(active=True).all()
    return render_template("collections/index.html", loans=active_loans,
                           methods=PAYMENT_METHODS, zones=zones)


@bp.route("/receipts")
@permission_required(P_VIEW)
def receipts():
    page, per_page = page_args()
    pagination = tenant_query(Repayment).order_by(
        Repayment.created_at.desc()).paginate(page=page, per_page=per_page,
                                              error_out=False)
    return render_template("collections/receipts.html", pagination=pagination)


@bp.route("/receipts/<int:repayment_id>")
@permission_required(P_VIEW)
def receipt(repayment_id):
    repayment = get_tenant_or_404(Repayment, repayment_id)
    return render_template("collections/receipt.html", r=repayment)


@bp.route("/receipts/<int:repayment_id>.pdf")
@permission_required(P_VIEW)
def receipt_pdf(repayment_id):
    repayment = get_tenant_or_404(Repayment, repayment_id)
    from mcag.services.pdf import render_pdf
    pdf = render_pdf("pdf/receipt.html", r=repayment,
                     institution=current_user.institution)
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition":
            f"attachment; filename=receipt-{repayment.receipt_number}.pdf"})


@bp.route("/receipts/<int:repayment_id>/reverse", methods=["POST"])
@permission_required(P_REVERSE_PAYMENT)
def reverse(repayment_id):
    repayment = get_tenant_or_404(Repayment, repayment_id)
    reason = request.form.get("reason") or ""
    if not reason:
        flash("A reversal reason is required.", "danger")
        return redirect(url_for("collections.receipt", repayment_id=repayment.id))
    try:
        reverse_repayment(repayment, reason, current_user, current_user.institution)
        from mcag.services.alerts import _raise_alert
        _raise_alert(current_user.institution_id, "unusual_reversal",
                     f"Receipt {repayment.receipt_number} reversed: {reason}",
                     repayment.loan.customer, "Repayment", repayment.id)
        db.session.commit()
        flash("Receipt reversed (the original receipt remains on record).",
              "success")
    except LoanServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("collections.receipt", repayment_id=repayment.id))


# ---------------------------------------------------------------------------
# Field collections
# ---------------------------------------------------------------------------
@bp.route("/field")
@permission_required(P_RECEIVE_REPAYMENT)
def field():
    zone_id = request.args.get("zone_id", type=int)
    today = date.today()
    zones = tenant_query(CollectionZone).filter_by(active=True).all()
    query = (tenant_query(ScheduleInstalment)
             .join(Loan, ScheduleInstalment.loan_id == Loan.id)
             .filter(Loan.status.in_([LOAN_ACTIVE, "Restructured"]),
                     ScheduleInstalment.due_date <= today,
                     ScheduleInstalment.status.in_(
                         ["Due", "Overdue", "Partly Paid", "Not Yet Due"])))
    if zone_id:
        query = query.filter(Loan.collection_zone_id == zone_id)
    instalments = [i for i in query.order_by(ScheduleInstalment.due_date).all()
                   if i.total_outstanding > 0]
    collected_today = tenant_query(Repayment).filter(
        Repayment.paid_at == today, Repayment.reversed.is_(False)).all()
    totals = {
        "expected": money(sum(D(i.total_outstanding) for i in instalments)),
        "collected": money(sum(D(r.amount) for r in collected_today)),
        "cash": money(sum(D(r.amount) for r in collected_today if r.method == "cash")),
        "momo": money(sum(D(r.amount) for r in collected_today
                          if r.method == "mobile_money")),
        "bank": money(sum(D(r.amount) for r in collected_today
                          if r.method == "bank_transfer")),
    }
    return render_template("collections/field.html", instalments=instalments,
                           zones=zones, zone_id=zone_id, totals=totals,
                           collected=collected_today)


# ---------------------------------------------------------------------------
# Cashbook (daily reconciliation)
# ---------------------------------------------------------------------------
@bp.route("/cashbook", methods=["GET", "POST"])
@permission_required(P_VIEW)
def cashbook():
    today = date.today()
    if request.method == "POST":
        book_date = date.fromisoformat(request.form.get("book_date")
                                       or today.isoformat())
        record = tenant_query(CashbookDay).filter_by(book_date=book_date).first()
        if record is None:
            previous = (tenant_query(CashbookDay)
                        .filter(CashbookDay.book_date < book_date)
                        .order_by(CashbookDay.book_date.desc()).first())
            record = CashbookDay(
                book_date=book_date,
                opening_balance=previous.closing_balance if previous else 0,
                prepared_by_id=current_user.id,
            )
            stamp_tenant(record)
            db.session.add(record)
        day_receipts = tenant_query(Repayment).filter(
            Repayment.paid_at == book_date, Repayment.reversed.is_(False),
            Repayment.method == "cash").all()
        from mcag.models import Disbursement
        day_disb = tenant_query(Disbursement).filter(
            Disbursement.disbursement_date == book_date,
            Disbursement.method == "cash",
            Disbursement.status == "completed").all()
        day_expenses = tenant_query(Expense).filter(
            Expense.expense_date == book_date,
            Expense.paid_from_subtype == "cash").all()
        record.cash_receipts = money(sum(D(r.amount) for r in day_receipts))
        record.cash_disbursements = money(sum(D(d.net_amount) for d in day_disb))
        record.expenses = money(sum(D(e.amount) for e in day_expenses))
        record.cash_handed_over = money(D(request.form.get("cash_handed_over") or 0))
        record.cash_banked = money(D(request.form.get("cash_banked") or 0))
        record.closing_balance = money(
            D(record.opening_balance) + D(record.cash_receipts)
            - D(record.cash_disbursements) - D(record.expenses)
            - D(record.cash_handed_over) - D(record.cash_banked))
        physical = request.form.get("physical_count")
        if physical:
            record.physical_count = money(D(physical))
            record.variance = money(D(record.physical_count) - D(record.closing_balance))
        record.status = "submitted"
        log_action("cashbook_submitted", "CashbookDay", None,
                   new_value={"date": str(book_date),
                              "closing": str(record.closing_balance)})
        db.session.commit()
        flash("Cashbook submitted for supervisor approval.", "success")
        return redirect(url_for("collections.cashbook"))
    records = tenant_query(CashbookDay).order_by(
        CashbookDay.book_date.desc()).limit(30).all()
    return render_template("collections/cashbook.html", records=records, today=today)


@bp.route("/cashbook/<int:day_id>/approve", methods=["POST"])
@permission_required(P_APPROVE)
def cashbook_approve(day_id):
    record = get_tenant_or_404(CashbookDay, day_id)
    if record.prepared_by_id == current_user.id:
        flash("Maker-checker control: the preparer cannot approve their own "
              "cashbook.", "danger")
        return redirect(url_for("collections.cashbook"))
    record.status = "approved"
    record.approved_by_id = current_user.id
    record.approved_at = utcnow()
    log_action("cashbook_approved", "CashbookDay", record.id)
    db.session.commit()
    flash("Cashbook day approved.", "success")
    return redirect(url_for("collections.cashbook"))


@bp.route("/daily-report")
@permission_required(P_VIEW)
def daily_report():
    report_date = (date.fromisoformat(request.args["d"])
                   if request.args.get("d") else date.today())
    repayments = tenant_query(Repayment).filter(
        Repayment.paid_at == report_date).order_by(Repayment.receipt_number).all()
    total = money(sum(D(r.amount) for r in repayments if not r.reversed))
    return render_template("collections/daily_report.html",
                           repayments=repayments, report_date=report_date,
                           total=total)
