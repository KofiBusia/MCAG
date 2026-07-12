"""Central loan calculation engine.

This is the ONLY place loan figures are computed. Staff can never enter
interest charge, total repayment, instalment amount, instalment count or
closing balances by hand — every offer letter, agreement and schedule is
generated from here, preventing errors like the MCAG sample offer letter
(GH¢2,500 principal + GH¢600 interest = GH¢3,100 over 13 payments stated
as GH¢43 each, which does not add up).

All money is Decimal, quantized to 2 dp. No binary floating point.
"""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from mcag.constants import (
    FREQ_FORTNIGHTLY, FREQ_MONTHLY, FREQ_WEEKLY, METHOD_FLAT, METHOD_REDUCING,
    PERIODS_PER_YEAR, SCHED_BULLET, SCHED_EQUAL_INSTALMENT, SCHED_EQUAL_PRINCIPAL,
)
from mcag.utils import D, money

ZERO = Decimal("0.00")


class LoanCalculationError(ValueError):
    """Raised when inputs are invalid or a schedule fails validation."""


def add_period(start: date, frequency: str, periods: int = 1) -> date:
    """Advance a date by N repayment periods."""
    if frequency == FREQ_WEEKLY:
        return start + timedelta(weeks=periods)
    if frequency == FREQ_FORTNIGHTLY:
        return start + timedelta(weeks=2 * periods)
    if frequency == FREQ_MONTHLY:
        month_index = start.month - 1 + periods
        year = start.year + month_index // 12
        month = month_index % 12 + 1
        day = min(start.day, monthrange(year, month)[1])
        return date(year, month, day)
    raise LoanCalculationError(f"Unknown repayment frequency: {frequency}")


def periodic_rate(rate_percent, rate_period: str, frequency: str) -> Decimal:
    """Convert a quoted rate (monthly or annual, in percent) to a per-period
    decimal fraction for the given repayment frequency."""
    rate = D(rate_percent) / Decimal("100")
    annual = rate * 12 if rate_period == "monthly" else rate
    return annual / Decimal(PERIODS_PER_YEAR[frequency])


def _annuity_payment(principal: Decimal, i: Decimal, n: int) -> Decimal:
    """Equal-instalment (annuity) payment using exact Decimal arithmetic."""
    if i == 0:
        return money(principal / n)
    factor = (Decimal(1) + i) ** n
    return money(principal * i * factor / (factor - 1))


def compute_fees(principal, application_fee=0, processing_fee_percent=0,
                 processing_fee_fixed=0, other_fees=0) -> dict:
    principal = D(principal)
    application_fee = money(application_fee)
    processing_fee = money(principal * D(processing_fee_percent) / 100) + money(processing_fee_fixed)
    other = money(other_fees)
    return {
        "application_fee": application_fee,
        "processing_fee": processing_fee,
        "other_fees": other,
        "total_fees": application_fee + processing_fee + other,
    }


def calculate_apr(net_received: Decimal, instalment_rows: list, frequency: str) -> Decimal:
    """Annual percentage rate solved as the IRR of actual cash flows
    (net amount received vs. instalments), annualized nominally.

    IRR root-finding uses float internally (a rate is not money); the result
    is rounded to 2 dp.
    """
    flows = [float(r["total_due"]) for r in instalment_rows]
    outlay = float(net_received)
    if outlay <= 0 or not flows or sum(flows) <= outlay:
        return Decimal("0.00")

    def npv(rate):
        total = -outlay
        for t, cf in enumerate(flows, start=1):
            total += cf / ((1 + rate) ** t)
        return total

    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    period_rate = (lo + hi) / 2
    apr = Decimal(str(round(period_rate * PERIODS_PER_YEAR[frequency] * 100, 2)))
    return apr


def build_schedule(
    principal,
    rate_percent,
    rate_period: str,
    interest_method: str,
    schedule_type: str,
    frequency: str,
    tenure: int,
    disbursement_date: date,
    grace_periods: int = 0,
    application_fee=0,
    processing_fee_percent=0,
    processing_fee_fixed=0,
    other_fees=0,
    fees_deducted_upfront: bool = True,
) -> dict:
    """Build a complete validated loan schedule.

    Returns a dict with all locked figures and instalment rows. Raises
    LoanCalculationError if inputs are invalid or internal validation fails.
    """
    principal = money(principal)
    tenure = int(tenure)
    grace_periods = int(grace_periods or 0)
    if principal <= 0:
        raise LoanCalculationError("Principal must be greater than zero.")
    if tenure < 1:
        raise LoanCalculationError("Tenure must be at least one instalment.")
    if D(rate_percent) < 0:
        raise LoanCalculationError("Interest rate cannot be negative.")
    if frequency not in PERIODS_PER_YEAR:
        raise LoanCalculationError(f"Unknown frequency: {frequency}")

    i = periodic_rate(rate_percent, rate_period, frequency)
    fees = compute_fees(principal, application_fee, processing_fee_percent,
                        processing_fee_fixed, other_fees)

    rows = []
    balance = principal
    due = disbursement_date

    # Grace periods: interest-only instalments at the start.
    for g in range(1, grace_periods + 1):
        due = add_period(due, frequency)
        interest = money(balance * i)
        rows.append({
            "number": g, "due_date": due, "opening_principal": balance,
            "principal_due": ZERO, "interest_due": interest, "fees_due": ZERO,
            "total_due": interest, "closing_principal": balance,
        })

    n = tenure
    start_no = grace_periods

    if interest_method == METHOD_FLAT:
        total_interest = money(principal * i * n)
        if schedule_type == SCHED_BULLET:
            per_interest = money(total_interest / n)
            for k in range(1, n + 1):
                due = add_period(due, frequency)
                interest = per_interest if k < n else total_interest - per_interest * (n - 1)
                principal_due = principal if k == n else ZERO
                rows.append({
                    "number": start_no + k, "due_date": due, "opening_principal": balance,
                    "principal_due": principal_due, "interest_due": interest, "fees_due": ZERO,
                    "total_due": principal_due + interest,
                    "closing_principal": balance - principal_due,
                })
                balance -= principal_due
        else:
            # flat + equal instalments (equal principal is identical under flat)
            per_principal = money(principal / n)
            per_interest = money(total_interest / n)
            for k in range(1, n + 1):
                due = add_period(due, frequency)
                if k == n:  # rounding differences corrected in final instalment
                    principal_due = balance
                    interest = total_interest - per_interest * (n - 1)
                else:
                    principal_due = per_principal
                    interest = per_interest
                rows.append({
                    "number": start_no + k, "due_date": due, "opening_principal": balance,
                    "principal_due": principal_due, "interest_due": interest, "fees_due": ZERO,
                    "total_due": principal_due + interest,
                    "closing_principal": balance - principal_due,
                })
                balance -= principal_due
    elif interest_method == METHOD_REDUCING:
        if schedule_type == SCHED_EQUAL_INSTALMENT:
            payment = _annuity_payment(balance, i, n)
            for k in range(1, n + 1):
                due = add_period(due, frequency)
                interest = money(balance * i)
                if k == n:
                    principal_due = balance  # force exact zero closing balance
                else:
                    principal_due = money(payment - interest)
                    if principal_due > balance:
                        principal_due = balance
                rows.append({
                    "number": start_no + k, "due_date": due, "opening_principal": balance,
                    "principal_due": principal_due, "interest_due": interest, "fees_due": ZERO,
                    "total_due": principal_due + interest,
                    "closing_principal": balance - principal_due,
                })
                balance -= principal_due
        elif schedule_type == SCHED_EQUAL_PRINCIPAL:
            per_principal = money(principal / n)
            for k in range(1, n + 1):
                due = add_period(due, frequency)
                interest = money(balance * i)
                principal_due = balance if k == n else per_principal
                rows.append({
                    "number": start_no + k, "due_date": due, "opening_principal": balance,
                    "principal_due": principal_due, "interest_due": interest, "fees_due": ZERO,
                    "total_due": principal_due + interest,
                    "closing_principal": balance - principal_due,
                })
                balance -= principal_due
        elif schedule_type == SCHED_BULLET:
            for k in range(1, n + 1):
                due = add_period(due, frequency)
                interest = money(balance * i)
                principal_due = balance if k == n else ZERO
                rows.append({
                    "number": start_no + k, "due_date": due, "opening_principal": balance,
                    "principal_due": principal_due, "interest_due": interest, "fees_due": ZERO,
                    "total_due": principal_due + interest,
                    "closing_principal": balance - principal_due,
                })
                balance -= principal_due
        else:
            raise LoanCalculationError(f"Unknown schedule type: {schedule_type}")
        total_interest = sum(r["interest_due"] for r in rows)
    else:
        raise LoanCalculationError(f"Unknown interest method: {interest_method}")

    total_interest = money(sum(r["interest_due"] for r in rows))
    total_repayment = money(sum(r["total_due"] for r in rows))
    total_fees = fees["total_fees"]
    net_received = principal - total_fees if fees_deducted_upfront else principal
    result = {
        "principal": principal,
        "rate_percent": money(rate_percent),
        "rate_period": rate_period,
        "periodic_rate": str(i),
        "interest_method": interest_method,
        "schedule_type": schedule_type,
        "frequency": frequency,
        "tenure": tenure,
        "grace_periods": grace_periods,
        "application_fee": fees["application_fee"],
        "processing_fee": fees["processing_fee"],
        "other_fees": fees["other_fees"],
        "total_fees": total_fees,
        "fees_deducted_upfront": fees_deducted_upfront,
        "gross_amount_financed": principal,
        "net_amount_received": money(net_received),
        "total_interest": total_interest,
        "total_repayment": total_repayment,
        "total_cost_of_credit": money(total_interest + total_fees),
        "number_of_instalments": len(rows),
        "instalment_amount": rows[0]["total_due"] if rows else ZERO,
        "first_due_date": rows[0]["due_date"] if rows else None,
        "final_due_date": rows[-1]["due_date"] if rows else None,
        "disbursement_date": disbursement_date,
        "instalments": rows,
    }
    result["apr"] = calculate_apr(result["net_amount_received"], rows, frequency)
    validate_schedule(result)
    return result


def validate_schedule(result: dict):
    """Internal consistency checks. Any failure blocks the schedule."""
    rows = result["instalments"]
    if len(rows) != result["tenure"] + result["grace_periods"]:
        raise LoanCalculationError("Instalment count does not match tenure plus grace periods.")

    total = money(sum(r["total_due"] for r in rows))
    if total != result["total_repayment"]:
        raise LoanCalculationError("Instalments do not add up to total repayment.")

    principal_total = money(sum(r["principal_due"] for r in rows))
    if principal_total != result["principal"]:
        raise LoanCalculationError("Scheduled principal does not equal the loan principal.")

    if rows and rows[-1]["closing_principal"] != ZERO:
        raise LoanCalculationError("Final closing balance is not zero.")

    interest_total = money(sum(r["interest_due"] for r in rows))
    if interest_total != result["total_interest"]:
        raise LoanCalculationError("Interest rows do not equal total interest.")

    if money(result["total_interest"] + result["principal"]) != result["total_repayment"]:
        raise LoanCalculationError("Principal plus interest does not equal total repayment.")

    # Dates strictly increasing
    for a, b in zip(rows, rows[1:]):
        if b["due_date"] <= a["due_date"]:
            raise LoanCalculationError("Repayment dates are not strictly increasing.")

    # Ledger-style continuity
    for a, b in zip(rows, rows[1:]):
        if a["closing_principal"] != b["opening_principal"]:
            raise LoanCalculationError("Opening/closing balances are not continuous.")


def serialize_calculation(result: dict) -> dict:
    """JSON-safe copy for immutable storage on offers/agreements."""
    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        return v
    out = {k: conv(v) for k, v in result.items() if k != "instalments"}
    out["instalments"] = [{k: conv(v) for k, v in r.items()} for r in result["instalments"]]
    return out


def early_settlement_quote(loan, as_of: date, early_charge_percent=0) -> dict:
    """Settlement figures as of a date: outstanding principal, accrued
    interest to date, penalties, plus any early settlement charge."""
    principal_out = D(loan.principal_outstanding)
    penalties = D(loan.penalties_outstanding)
    # Interest accrued: interest due on instalments up to and including as_of
    # that remains unpaid.
    interest_due = ZERO
    for inst in loan.instalments:
        if inst.due_date <= as_of:
            interest_due += D(inst.interest_due) - D(inst.interest_paid)
    interest_due = max(interest_due, ZERO)
    charge = money(principal_out * D(early_charge_percent) / 100)
    total = money(principal_out + interest_due + penalties + charge)
    return {
        "principal_outstanding": money(principal_out),
        "interest_accrued": money(interest_due),
        "early_settlement_charge": charge,
        "penalties": money(penalties),
        "waivers": ZERO,
        "total_settlement": total,
    }
