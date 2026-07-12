"""Arrears classification, PAR and provisioning calculations."""
from datetime import date
from decimal import Decimal

from mcag.constants import ARREARS_BUCKETS, DEFAULT_PROVISION_RATES, LOAN_ACTIVE, LOAN_WRITTEN_OFF
from mcag.models import Loan
from mcag.utils import D, money

ZERO = Decimal("0.00")


def bucket_for_days(days: int) -> str:
    for key, _label, lo, hi in ARREARS_BUCKETS:
        if lo <= days <= hi:
            return key
    return "d180_plus"


def classify_portfolio(institution, as_of: date = None) -> dict:
    """Classify all loans into arrears buckets and compute PAR / provisioning."""
    as_of = as_of or date.today()
    rates = {**DEFAULT_PROVISION_RATES, **institution.setting("provision_rates", {})}

    buckets = {key: {"label": label, "count": 0, "principal": ZERO, "provision": ZERO}
               for key, label, _lo, _hi in ARREARS_BUCKETS}
    written_off = {"count": 0, "principal": ZERO}
    gross_portfolio = ZERO
    par_amounts = {1: ZERO, 30: ZERO, 60: ZERO, 90: ZERO}
    npl = ZERO
    loans = Loan.query.filter(
        Loan.institution_id == institution.id,
        Loan.status.in_([LOAN_ACTIVE, "Restructured", LOAN_WRITTEN_OFF]),
    ).all()

    detail = []
    for loan in loans:
        if loan.status == LOAN_WRITTEN_OFF:
            written_off["count"] += 1
            written_off["principal"] = money(written_off["principal"] + D(loan.principal_outstanding))
            continue
        principal_out = D(loan.principal_outstanding)
        gross_portfolio = money(gross_portfolio + principal_out)
        days = loan.days_overdue(as_of)
        key = bucket_for_days(days)
        b = buckets[key]
        b["count"] += 1
        b["principal"] = money(b["principal"] + principal_out)
        for threshold in par_amounts:
            if days >= threshold:
                par_amounts[threshold] = money(par_amounts[threshold] + principal_out)
        if days > 90:
            npl = money(npl + principal_out)
        detail.append({"loan": loan, "days_overdue": days, "bucket": key,
                       "principal_outstanding": principal_out})

    provision_required = ZERO
    for key, b in buckets.items():
        rate = D(rates.get(key, "0")) / 100
        b["rate_percent"] = D(rates.get(key, "0"))
        b["provision"] = money(b["principal"] * rate)
        provision_required = money(provision_required + b["provision"])

    def ratio(part):
        return money(part / gross_portfolio * 100) if gross_portfolio > 0 else ZERO

    return {
        "as_of": as_of,
        "buckets": buckets,
        "written_off": written_off,
        "gross_portfolio": gross_portfolio,
        "performing": buckets["current"]["principal"],
        "in_arrears": money(gross_portfolio - buckets["current"]["principal"]),
        "par": {t: {"amount": amt, "ratio": ratio(amt)} for t, amt in par_amounts.items()},
        "npl": {"amount": npl, "ratio": ratio(npl)},
        "provision_required": provision_required,
        "detail": detail,
    }


def collection_rate(institution_id: int, from_date: date, to_date: date) -> dict:
    """Amount collected vs amount due within a period."""
    from mcag.models import Repayment, ScheduleInstalment

    from mcag.extensions import db
    due = (db.session.query(db.func.coalesce(db.func.sum(ScheduleInstalment.total_due), 0))
           .filter(ScheduleInstalment.institution_id == institution_id,
                   ScheduleInstalment.due_date >= from_date,
                   ScheduleInstalment.due_date <= to_date)
           .scalar())
    collected = (db.session.query(db.func.coalesce(db.func.sum(Repayment.amount), 0))
                 .filter(Repayment.institution_id == institution_id,
                         Repayment.reversed.is_(False),
                         Repayment.paid_at >= from_date,
                         Repayment.paid_at <= to_date)
                 .scalar())
    due, collected = D(due), D(collected)
    rate = money(collected / due * 100) if due > 0 else ZERO
    return {"due": money(due), "collected": money(collected), "rate": rate}
