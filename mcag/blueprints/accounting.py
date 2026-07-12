"""Accounting: chart of accounts, journals, expenses, financial reports,
funding register."""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import FUNDING_SOURCES, P_POST_JOURNAL, P_VIEW
from mcag.extensions import db
from mcag.models import Account, Expense, FundingSource, JournalEntry, JournalLine
from mcag.services.accounting import (
    AccountingError, balance_sheet, get_account, income_statement, post_journal,
    trial_balance,
)
from mcag.services.audit import log_action
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D, money

bp = Blueprint("accounting", __name__)


@bp.route("/accounts")
@permission_required(P_VIEW)
def accounts():
    records = tenant_query(Account).order_by(Account.code).all()
    return render_template("accounting/accounts.html", accounts=records)


@bp.route("/journals", methods=["GET", "POST"])
@permission_required(P_VIEW)
def journals():
    if request.method == "POST":
        if not current_user.can(P_POST_JOURNAL):
            flash("You do not have permission to post journals.", "danger")
            return redirect(url_for("accounting.journals"))
        entry_date = date.fromisoformat(request.form.get("entry_date")
                                        or date.today().isoformat())
        description = request.form.get("description") or "Manual journal"
        lines = []
        for i in range(1, 7):
            account_id = request.form.get(f"account_id_{i}", type=int)
            debit = D(request.form.get(f"debit_{i}") or 0)
            credit = D(request.form.get(f"credit_{i}") or 0)
            if account_id and (debit > 0 or credit > 0):
                account = get_tenant_or_404(Account, account_id)
                lines.append((account, debit, credit))
        try:
            entry = post_journal(current_user.institution, entry_date,
                                 description, lines, source="manual",
                                 user=current_user)
            log_action("journal_posted", "JournalEntry", None,
                       new_value={"number": entry.journal_number,
                                  "description": description})
            db.session.commit()
            flash(f"Journal {entry.journal_number} posted.", "success")
        except AccountingError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("accounting.journals"))
    records = (tenant_query(JournalEntry)
               .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
               .limit(100).all())
    account_list = tenant_query(Account).filter_by(active=True).order_by(Account.code).all()
    return render_template("accounting/journals.html", journals=records,
                           accounts=account_list)


@bp.route("/expenses", methods=["GET", "POST"])
@permission_required(P_VIEW)
def expenses():
    expense_accounts = (tenant_query(Account)
                        .filter_by(type="expense", active=True)
                        .order_by(Account.code).all())
    if request.method == "POST":
        if not current_user.can(P_POST_JOURNAL):
            flash("You do not have permission to record expenses.", "danger")
            return redirect(url_for("accounting.expenses"))
        account = get_tenant_or_404(Account, request.form.get("account_id", type=int))
        amount = money(D(request.form.get("amount") or 0))
        if amount <= 0:
            flash("Expense amount must be positive.", "danger")
            return redirect(url_for("accounting.expenses"))
        paid_from = request.form.get("paid_from") or "cash"
        expense = Expense(
            expense_date=date.fromisoformat(request.form.get("expense_date")
                                            or date.today().isoformat()),
            account_id=account.id,
            paid_from_subtype=paid_from,
            amount=amount,
            payee=request.form.get("payee"),
            description=request.form.get("description") or account.name,
            reference=request.form.get("reference"),
            recorded_by_id=current_user.id,
        )
        stamp_tenant(expense)
        db.session.add(expense)
        try:
            entry = post_journal(
                current_user.institution, expense.expense_date,
                f"Expense: {expense.description}",
                [(account, amount, D(0)), (paid_from, D(0), amount)],
                source="expense", user=current_user)
            expense.journal_entry_id = entry.id
            log_action("expense_recorded", "Expense", None,
                       new_value={"amount": str(amount),
                                  "account": account.name})
            db.session.commit()
            flash("Expense recorded and posted.", "success")
        except AccountingError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("accounting.expenses"))
    records = tenant_query(Expense).order_by(Expense.expense_date.desc()).limit(100).all()
    return render_template("accounting/expenses.html", expenses=records,
                           accounts=expense_accounts)


@bp.route("/trial-balance")
@permission_required(P_VIEW)
def trial_balance_view():
    to_date = (date.fromisoformat(request.args["to"])
               if request.args.get("to") else date.today())
    rows = trial_balance(current_user.institution_id, to_date=to_date)
    totals = {
        "debit": money(sum(r["debit"] for r in rows)),
        "credit": money(sum(r["credit"] for r in rows)),
    }
    return render_template("accounting/trial_balance.html", rows=rows,
                           totals=totals, to_date=to_date)


@bp.route("/income-statement")
@permission_required(P_VIEW)
def income_statement_view():
    today = date.today()
    from_date = (date.fromisoformat(request.args["from"])
                 if request.args.get("from") else today.replace(day=1))
    to_date = (date.fromisoformat(request.args["to"])
               if request.args.get("to") else today)
    data = income_statement(current_user.institution_id, from_date, to_date)
    return render_template("accounting/income_statement.html", data=data,
                           from_date=from_date, to_date=to_date)


@bp.route("/balance-sheet")
@permission_required(P_VIEW)
def balance_sheet_view():
    to_date = (date.fromisoformat(request.args["to"])
               if request.args.get("to") else date.today())
    data = balance_sheet(current_user.institution_id, to_date=to_date)
    return render_template("accounting/balance_sheet.html", data=data,
                           to_date=to_date)


@bp.route("/general-ledger")
@permission_required(P_VIEW)
def general_ledger():
    account_id = request.args.get("account_id", type=int)
    account_list = tenant_query(Account).order_by(Account.code).all()
    lines = []
    account = None
    if account_id:
        account = get_tenant_or_404(Account, account_id)
        lines = (tenant_query(JournalLine)
                 .filter(JournalLine.account_id == account.id)
                 .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
                 .order_by(JournalEntry.entry_date, JournalEntry.id).all())
    return render_template("accounting/general_ledger.html",
                           accounts=account_list, account=account, lines=lines)


# ---------------------------------------------------------------------------
# Funding register
# ---------------------------------------------------------------------------
@bp.route("/funding", methods=["GET", "POST"])
@permission_required(P_VIEW)
def funding():
    if request.method == "POST":
        if not current_user.can(P_POST_JOURNAL):
            flash("You do not have permission to record funding.", "danger")
            return redirect(url_for("accounting.funding"))
        record = FundingSource(
            source_type=request.form.get("source_type") or "other",
            provider_name=request.form.get("provider_name") or "",
            amount=money(D(request.form.get("amount") or 0)),
            date_received=date.fromisoformat(request.form.get("date_received")
                                             or date.today().isoformat()),
            interest_rate=request.form.get("interest_rate") or None,
            tenure_months=request.form.get("tenure_months", type=int),
            repayment_terms=request.form.get("repayment_terms"),
            security_given=request.form.get("security_given"),
            notes=request.form.get("notes"),
            recorded_by_id=current_user.id,
        )
        record.outstanding_balance = record.amount
        if not record.provider_name or D(record.amount) <= 0:
            flash("Provider name and a positive amount are required.", "danger")
            return redirect(url_for("accounting.funding"))
        stamp_tenant(record)
        db.session.add(record)
        # Post: Dr Bank, Cr Capital or Borrowings
        target = ("capital" if record.source_type in
                  ("owner_capital", "shareholder", "retained_earnings", "donor")
                  else "borrowings")
        try:
            post_journal(current_user.institution, record.date_received,
                         f"Funding: {record.provider_name}",
                         [("bank", record.amount, D(0)),
                          (target, D(0), record.amount)],
                         source="funding", user=current_user)
            log_action("funding_recorded", "FundingSource", None,
                       new_value={"provider": record.provider_name,
                                  "amount": str(record.amount)})
            db.session.commit()
            flash("Funding recorded. Reminder: customer deposits and public "
                  "savings must NOT be recorded as lending capital.", "success")
        except AccountingError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
        return redirect(url_for("accounting.funding"))
    records = tenant_query(FundingSource).order_by(
        FundingSource.date_received.desc()).all()
    return render_template("accounting/funding.html", records=records,
                           source_types=FUNDING_SOURCES)
