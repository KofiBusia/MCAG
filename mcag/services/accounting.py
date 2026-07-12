"""Accounting service: chart of accounts, automatic journal posting."""
from datetime import date
from decimal import Decimal

from mcag.constants import DEFAULT_CHART_OF_ACCOUNTS
from mcag.extensions import db
from mcag.models.accounting import Account, JournalEntry, JournalLine
from mcag.utils import D, money


class AccountingError(ValueError):
    pass


def seed_chart_of_accounts(institution):
    """Create the default chart of accounts for a new institution."""
    existing = {a.code for a in Account.query.filter_by(institution_id=institution.id)}
    for code, name, type_, subtype in DEFAULT_CHART_OF_ACCOUNTS:
        if code not in existing:
            db.session.add(Account(
                institution_id=institution.id, code=code, name=name,
                type=type_, subtype=subtype,
            ))


def get_account(institution_id: int, subtype: str) -> Account:
    account = Account.query.filter_by(
        institution_id=institution_id, subtype=subtype, active=True
    ).first()
    if account is None:
        raise AccountingError(f"No active account with subtype '{subtype}'. Seed the chart of accounts.")
    return account


def post_journal(institution, entry_date: date, description: str, lines: list,
                 source: str = "manual", reference_id=None, user=None) -> JournalEntry:
    """Post a balanced journal entry.

    lines: list of (account, debit, credit) tuples where account is an
    Account instance or a subtype string.
    """
    total_debit = money(sum(D(l[1]) for l in lines))
    total_credit = money(sum(D(l[2]) for l in lines))
    if total_debit != total_credit:
        raise AccountingError(
            f"Journal entry is not balanced: debits {total_debit} != credits {total_credit}")
    if total_debit == 0:
        raise AccountingError("Journal entry has no value.")

    seq = institution.take_sequence("next_journal_seq")
    entry = JournalEntry(
        institution_id=institution.id,
        journal_number=f"JRN-{seq:06d}",
        entry_date=entry_date,
        description=description[:255],
        source=source,
        reference_id=reference_id,
        posted_by_id=getattr(user, "id", None),
    )
    db.session.add(entry)
    db.session.flush()
    for account, debit, credit in lines:
        if isinstance(account, str):
            account = get_account(institution.id, account)
        db.session.add(JournalLine(
            institution_id=institution.id,
            journal_entry_id=entry.id,
            account_id=account.id,
            debit=money(debit), credit=money(credit),
        ))
    return entry


def account_balance(institution_id: int, account: Account,
                    from_date: date = None, to_date: date = None) -> Decimal:
    """Signed balance for an account (positive in its normal direction)."""
    q = (db.session.query(
            db.func.coalesce(db.func.sum(JournalLine.debit), 0),
            db.func.coalesce(db.func.sum(JournalLine.credit), 0))
         .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
         .filter(JournalLine.institution_id == institution_id,
                 JournalLine.account_id == account.id))
    if from_date:
        q = q.filter(JournalEntry.entry_date >= from_date)
    if to_date:
        q = q.filter(JournalEntry.entry_date <= to_date)
    debit, credit = q.one()
    debit, credit = D(debit), D(credit)
    return money(debit - credit) if account.is_debit_normal else money(credit - debit)


def trial_balance(institution_id: int, to_date: date = None) -> list:
    rows = []
    for account in (Account.query.filter_by(institution_id=institution_id, active=True)
                    .order_by(Account.code)):
        bal = account_balance(institution_id, account, to_date=to_date)
        debit = bal if account.is_debit_normal and bal >= 0 else (
            -bal if not account.is_debit_normal and bal < 0 else Decimal("0.00"))
        credit = bal if not account.is_debit_normal and bal >= 0 else (
            -bal if account.is_debit_normal and bal < 0 else Decimal("0.00"))
        rows.append({"account": account, "balance": bal,
                     "debit": money(debit), "credit": money(credit)})
    return rows


def income_statement(institution_id: int, from_date: date, to_date: date) -> dict:
    income, expenses = [], []
    total_income = total_expense = Decimal("0.00")
    for account in (Account.query.filter_by(institution_id=institution_id, active=True)
                    .filter(Account.type.in_(["income", "expense"]))
                    .order_by(Account.code)):
        bal = account_balance(institution_id, account, from_date=from_date, to_date=to_date)
        row = {"account": account, "amount": bal}
        if account.type == "income":
            income.append(row); total_income += bal
        else:
            expenses.append(row); total_expense += bal
    return {
        "income": income, "expenses": expenses,
        "total_income": money(total_income),
        "total_expenses": money(total_expense),
        "net_profit": money(total_income - total_expense),
    }


def balance_sheet(institution_id: int, to_date: date = None) -> dict:
    assets, liabilities, equity = [], [], []
    totals = {"asset": Decimal("0.00"), "liability": Decimal("0.00"), "equity": Decimal("0.00")}
    for account in (Account.query.filter_by(institution_id=institution_id, active=True)
                    .filter(Account.type.in_(["asset", "liability", "equity"]))
                    .order_by(Account.code)):
        bal = account_balance(institution_id, account, to_date=to_date)
        row = {"account": account, "amount": bal}
        if account.type == "asset":
            # contra provision account reduces assets naturally (credit balance -> negative)
            assets.append(row); totals["asset"] += bal
        elif account.type == "liability":
            liabilities.append(row); totals["liability"] += bal
        else:
            equity.append(row); totals["equity"] += bal

    # Retained current-period earnings
    inc = income_statement(institution_id, date(2000, 1, 1), to_date or date.today())
    current_earnings = inc["net_profit"]
    return {
        "assets": assets, "liabilities": liabilities, "equity": equity,
        "total_assets": money(totals["asset"]),
        "total_liabilities": money(totals["liability"]),
        "total_equity": money(totals["equity"] + current_earnings),
        "current_earnings": current_earnings,
        "balanced": money(totals["asset"]) == money(
            totals["liability"] + totals["equity"] + current_earnings),
    }
