"""Management dashboard for institution staff."""
from datetime import date

from flask import Blueprint, render_template
from flask_login import current_user

from mcag.constants import LOAN_ACTIVE, P_VIEW
from mcag.blueprints.helpers import permission_required
from mcag.extensions import db
from mcag.models import (
    Complaint, Customer, DuplicateAlert, Loan, Repayment, ScheduleInstalment,
)
from mcag.services.accounting import account_balance, get_account, income_statement
from mcag.services.arrears import classify_portfolio, collection_rate
from mcag.services.tenancy import tenant_query
from mcag.utils import D, money

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@permission_required(P_VIEW)
def home():
    inst = current_user.institution
    today = date.today()
    month_start = today.replace(day=1)

    total_customers = tenant_query(Customer).count()
    active_loans = tenant_query(Loan).filter(Loan.status == LOAN_ACTIVE)
    active_borrowers = active_loans.with_entities(
        db.func.count(db.func.distinct(Loan.customer_id))).scalar() or 0
    disbursed_total = tenant_query(Loan).with_entities(
        db.func.coalesce(db.func.sum(Loan.principal), 0)).filter(
        Loan.disbursed_at.isnot(None)).scalar()
    outstanding = active_loans.with_entities(
        db.func.coalesce(db.func.sum(Loan.principal_outstanding), 0),
        db.func.coalesce(db.func.sum(Loan.interest_outstanding), 0)).one()

    collected_today = tenant_query(Repayment).filter(
        Repayment.paid_at == today, Repayment.reversed.is_(False)).with_entities(
        db.func.coalesce(db.func.sum(Repayment.amount), 0)).scalar()
    collected_month = tenant_query(Repayment).filter(
        Repayment.paid_at >= month_start, Repayment.reversed.is_(False)).with_entities(
        db.func.coalesce(db.func.sum(Repayment.amount), 0)).scalar()

    due_today = tenant_query(ScheduleInstalment).filter(
        ScheduleInstalment.due_date == today).count()

    portfolio = classify_portfolio(inst, today)
    month_collection = collection_rate(inst.id, month_start, today)
    income = income_statement(inst.id, month_start, today)

    try:
        cash_balance = account_balance(inst.id, get_account(inst.id, "cash"))
        bank_balance = account_balance(inst.id, get_account(inst.id, "bank"))
    except Exception:
        cash_balance = bank_balance = money(0)

    open_complaints = tenant_query(Complaint).filter(
        Complaint.status.in_(["open", "investigating", "escalated"])).count()
    open_alerts = tenant_query(DuplicateAlert).filter(
        DuplicateAlert.status == "open").count()

    deadlines = []
    if inst.regulatory_renewal_date:
        deadlines.append(("Regulatory renewal", inst.regulatory_renewal_date))
    if inst.mcag_renewal_date:
        deadlines.append(("MCAG membership renewal", inst.mcag_renewal_date))

    return render_template(
        "dashboard/home.html",
        total_customers=total_customers,
        active_borrowers=active_borrowers,
        disbursed_total=D(disbursed_total),
        principal_outstanding=D(outstanding[0]),
        interest_outstanding=D(outstanding[1]),
        collected_today=D(collected_today),
        collected_month=D(collected_month),
        due_today=due_today,
        portfolio=portfolio,
        month_collection=month_collection,
        income=income,
        cash_balance=cash_balance,
        bank_balance=bank_balance,
        open_complaints=open_complaints,
        open_alerts=open_alerts,
        deadlines=deadlines,
    )
