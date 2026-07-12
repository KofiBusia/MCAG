"""MCAG Members Reporting Template (MRT) generation.

Reproduces the official MCAG-MRT_V2022_v8 workbook. All figures derive from
the loan ledger, accounting records and customer records — users never type
totals. Export fills the genuine template (kept in source_documents/) so the
association's own validation formulas remain intact.
"""
import io
import json
import os
from datetime import date, timedelta
from decimal import Decimal

from flask import current_app

from mcag.constants import (
    LOAN_ACTIVE, LOAN_PURPOSE_SECTORS, LOAN_WRITTEN_OFF, MRT_AGEING_BUCKETS,
    MRT_REGION_CODES, ROLE_CREDIT_OFFICER, ROLE_LOAN_OFFICER, ROLE_MANAGER,
    ROLE_INSTITUTION_ADMIN, ROLE_PROPRIETOR,
)
from mcag.extensions import db
from mcag.models import (
    Customer, JournalEntry, JournalLine, Loan, Repayment, User,
)
from mcag.services.accounting import (
    account_balance, balance_sheet, income_statement,
)
from mcag.models.accounting import Account
from mcag.utils import D, calculate_age, money

ZERO = Decimal("0.00")

QUARTERS = {
    "Q1": (1, 3), "Q2": (4, 6), "Q3": (7, 9), "Q4": (10, 12),
}

TEMPLATE_FILENAME = "MCAG-MRT_V2022_v8 (8).xlsx"


def quarter_dates(year: int, quarter: str):
    start_month, end_month = QUARTERS[quarter]
    start = date(year, start_month, 1)
    if end_month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, end_month + 1, 1) - timedelta(days=1)
    return start, end


def _income_received(institution_id, subtype, start, end):
    """Cash-basis income actually received in the period (per MRT definitions):
    credits to the income account from repayment/disbursement/recovery journals."""
    q = (db.session.query(
            db.func.coalesce(db.func.sum(JournalLine.credit - JournalLine.debit), 0))
         .join(JournalEntry, JournalLine.journal_entry_id == JournalEntry.id)
         .join(Account, JournalLine.account_id == Account.id)
         .filter(JournalLine.institution_id == institution_id,
                 Account.subtype == subtype,
                 JournalEntry.entry_date >= start,
                 JournalEntry.entry_date <= end))
    return money(D(q.scalar()))


def build_return_data(institution, year: int, quarter: str) -> dict:
    """Compute the full MRT dataset for a reporting quarter."""
    start, end = quarter_dates(year, quarter)
    inst_id = institution.id

    # ---- Portfolio ---------------------------------------------------------
    loans = Loan.query.filter(Loan.institution_id == inst_id).all()
    active_loans = [l for l in loans if l.status in (LOAN_ACTIVE, "Restructured")
                    and l.disbursed_at and l.disbursed_at.date() <= end]

    ageing = {key: {"label": label, "count": 0, "value": ZERO,
                    "min_rate": D(rate) / 100, "min_prov": ZERO, "actual_prov": ZERO}
              for key, label, _lo, _hi, rate in MRT_AGEING_BUCKETS}
    rescheduled = {"count": 0, "value": ZERO}
    gross_portfolio = ZERO
    for loan in active_loans:
        principal_out = D(loan.principal_outstanding)
        gross_portfolio = money(gross_portfolio + principal_out)
        days = loan.days_overdue(end)
        if loan.status == "Restructured" and days == 0:
            rescheduled["count"] += 1
            rescheduled["value"] = money(rescheduled["value"] + principal_out)
            continue
        for key, _label, lo, hi, _rate in MRT_AGEING_BUCKETS:
            if lo <= days <= hi:
                ageing[key]["count"] += 1
                ageing[key]["value"] = money(ageing[key]["value"] + principal_out)
                break

    provision_rates = institution.setting("mrt_provision_rates", {})
    total_min_prov = ZERO
    total_actual_prov = ZERO
    for (key, _label, _lo, _hi, min_rate) in MRT_AGEING_BUCKETS:
        b = ageing[key]
        b["min_prov"] = money(b["value"] * b["min_rate"])
        actual_rate = D(provision_rates.get(key, min_rate)) / 100
        b["actual_prov"] = max(money(b["value"] * actual_rate), b["min_prov"])
        total_min_prov = money(total_min_prov + b["min_prov"])
        total_actual_prov = money(total_actual_prov + b["actual_prov"])
    resched_prov = rescheduled["value"]  # 100%
    total_min_prov = money(total_min_prov + resched_prov)
    total_actual_prov = money(total_actual_prov + resched_prov)

    disbursed = [l for l in loans if l.disbursed_at
                 and start <= l.disbursed_at.date() <= end]
    written_off = [l for l in loans if l.written_off_at
                   and start <= l.written_off_at.date() <= end]
    top10 = sorted((D(l.principal_outstanding) for l in active_loans), reverse=True)[:10]

    # ---- Sector breakdown --------------------------------------------------
    sectors = {s: {"count": 0, "value": ZERO} for s in LOAN_PURPOSE_SECTORS}
    for loan in active_loans:
        sector = loan.purpose_sector if loan.purpose_sector in sectors else "Others"
        sectors[sector]["count"] += 1
        sectors[sector]["value"] = money(sectors[sector]["value"] + D(loan.principal_outstanding))

    # ---- Income statement (cash-received basis for portfolio income) -------
    interest_received = _income_received(inst_id, "interest_income", start, end)
    fees_received = money(_income_received(inst_id, "fee_income", start, end)
                          + _income_received(inst_id, "penalty_income", start, end))
    recoveries = _income_received(inst_id, "recovery_income", start, end)
    inc = income_statement(inst_id, start, end)
    staff_costs = ZERO
    other_admin = ZERO
    provision_expense = ZERO
    other_revenue = ZERO
    for row in inc["expenses"]:
        sub = row["account"].subtype
        if sub == "staff_costs":
            staff_costs = money(staff_costs + row["amount"])
        elif sub == "provision_expense":
            provision_expense = money(provision_expense + row["amount"])
        elif sub == "bad_debt":
            provision_expense = money(provision_expense + row["amount"])
        else:
            other_admin = money(other_admin + row["amount"])
    for row in inc["income"]:
        if row["account"].subtype not in ("interest_income", "fee_income",
                                          "penalty_income", "recovery_income"):
            other_revenue = money(other_revenue + row["amount"])

    # ---- Balance sheet -----------------------------------------------------
    bs = balance_sheet(inst_id, to_date=end)
    def bs_subtype(subtype):
        total = ZERO
        for row in bs["assets"] + bs["liabilities"] + bs["equity"]:
            if row["account"].subtype == subtype:
                total = money(total + row["amount"])
        return total

    cash_and_bank = money(bs_subtype("cash") + bs_subtype("bank"))
    fixed_assets = bs_subtype("fixed_asset")
    borrowings = bs_subtype("borrowings")
    payables = bs_subtype("payables")
    capital = bs_subtype("capital")
    retained = bs_subtype("retained_earnings")

    # ---- Outreach ----------------------------------------------------------
    customers = Customer.query.filter(Customer.institution_id == inst_id).all()
    new_clients = [c for c in customers if start <= c.created_at.date() <= end]
    active_borrower_ids = {l.customer_id for l in active_loans}
    active_female_ids = {l.customer_id for l in active_loans
                         if l.customer and (l.customer.sex or "").lower().startswith("f")}
    new_borrower_ids = {l.customer_id for l in disbursed}
    youth_loans = [l for l in active_loans if l.customer and l.customer.date_of_birth
                   and 18 <= (calculate_age(l.customer.date_of_birth, end) or 0) <= 35]
    women_disbursed = money(sum(D(l.principal) for l in disbursed
                                if l.customer and (l.customer.sex or "").lower().startswith("f")))

    staff = User.query.filter(User.institution_id == inst_id,
                              User.is_active_user.is_(True)).all()
    officers = [u for u in staff if u.role in (ROLE_LOAN_OFFICER, ROLE_CREDIT_OFFICER)]
    mgmt = [u for u in staff if u.role in (ROLE_MANAGER, ROLE_INSTITUTION_ADMIN, ROLE_PROPRIETOR)]

    data = {
        "institution": {
            "member_name": institution.legal_name,
            "member_code": institution.mcag_membership_number or "",
            "return_code": "MCAG-PR-UA",
            "year": year,
            "quarter": quarter,
            "region": MRT_REGION_CODES.get(
                (institution.setting("mrt_region") or ""), None)
                or next((v for k, v in MRT_REGION_CODES.items()
                         if k.lower() in (institution.office_address or "").lower()),
                        "Greater Accra-GA"),
        },
        "income_statement": {
            "interest_on_portfolio": interest_received,
            "fees_and_commissions": fees_received,
            "other_operating_revenue": other_revenue,
            "provision_for_impairment": provision_expense,
            "loans_recovered": recoveries,
            "personnel_expense": staff_costs,
            "other_admin_expense": other_admin,
            "total_income": inc["total_income"],
            "total_expenses": inc["total_expenses"],
            "net_profit": inc["net_profit"],
        },
        "balance_sheet": {
            "cash_and_bank": cash_and_bank,
            "gross_loan_portfolio": gross_portfolio,
            "impairment_allowance": total_actual_prov,
            "fixed_assets": fixed_assets,
            "borrowings": borrowings,
            "payables": payables,
            "paid_up_capital": capital,
            "income_surplus_prior": retained,
            "total_assets": bs["total_assets"],
        },
        "portfolio": {
            "loans_disbursed_count": len(disbursed),
            "loans_disbursed_value": money(sum(D(l.principal) for l in disbursed)),
            "loans_outstanding_count": len(active_loans),
            "loans_outstanding_value": gross_portfolio,
            "written_off_count": len(written_off),
            "written_off_value": money(sum(D(l.principal_outstanding) for l in written_off)),
            "top10_value": money(sum(top10)),
            "ageing": {k: {kk: (str(vv) if isinstance(vv, Decimal) else vv)
                           for kk, vv in v.items()} for k, v in ageing.items()},
            "rescheduled": {"count": rescheduled["count"], "value": str(rescheduled["value"])},
            "total_min_provision": total_min_prov,
            "total_actual_provision": total_actual_prov,
        },
        "sectors": {k: {"count": v["count"], "value": str(v["value"])}
                    for k, v in sectors.items()},
        "outreach": {
            "new_clients": len(new_clients),
            "new_female_clients": len([c for c in new_clients
                                       if (c.sex or "").lower().startswith("f")]),
            "new_borrowers": len(new_borrower_ids),
            "new_female_borrowers": len({l.customer_id for l in disbursed
                                         if l.customer and (l.customer.sex or "").lower().startswith("f")}),
            "total_clients": len(customers),
            "female_clients": len([c for c in customers
                                   if (c.sex or "").lower().startswith("f")]),
            "active_borrowers": len(active_borrower_ids),
            "active_female_borrowers": len(active_female_ids),
            "youth_with_loans": len({l.customer_id for l in youth_loans}),
            "youth_loans_value": money(sum(D(l.principal_outstanding) for l in youth_loans)),
            "loans_to_women_value": women_disbursed,
        },
        "staff": {
            "total": len(staff),
            "female": 0,  # sex not tracked for staff; institutions may adjust
            "loan_officers": len(officers),
            "management": len(mgmt),
        },
    }
    data["validation"] = validate_return(data)
    return data


def validate_return(data: dict) -> list:
    """MRT reconciliation checks. Errors must be fixed at source."""
    errors = []
    p = data["portfolio"]
    ageing_total = money(sum(D(v["value"]) for v in p["ageing"].values())
                         + D(p["rescheduled"]["value"]))
    if ageing_total != D(p["loans_outstanding_value"]):
        errors.append("Portfolio ageing does not add up to gross loan portfolio.")
    sector_total = money(sum(D(v["value"]) for v in data["sectors"].values()))
    if sector_total != D(p["loans_outstanding_value"]):
        errors.append("Sector breakdown does not add up to gross loan portfolio.")
    if D(p["total_actual_provision"]) < D(p["total_min_provision"]):
        errors.append("Actual provision is below the MCAG minimum required provision.")
    bs = data["balance_sheet"]
    if D(bs["gross_loan_portfolio"]) == 0:
        errors.append("Gross loan portfolio is zero — the MRT requires a non-zero portfolio.")
    if D(bs["cash_and_bank"]) == 0:
        errors.append("Cash on hand and bank is zero — the MRT requires a non-zero balance.")
    if data["outreach"]["active_borrowers"] != p["loans_outstanding_count"]:
        # informational: multiple loans per borrower make these differ
        pass
    return errors


def _template_path() -> str:
    return os.path.join(current_app.root_path, "..", "source_documents", TEMPLATE_FILENAME)


def export_to_excel(data: dict) -> bytes:
    """Fill the official MRT workbook with the computed data and return
    xlsx bytes. Template formulas and validations stay intact."""
    import openpyxl

    wb = openpyxl.load_workbook(_template_path())
    inst = data["institution"]

    cover = wb["Cover Sheet"]
    cover["C4"] = inst["member_name"]
    cover["C5"] = inst["member_code"]
    cover["C7"] = inst["year"]
    cover["C8"] = inst["quarter"]
    cover["C10"] = inst["region"]

    pl = wb["Profit & Loss Statement"]
    isd = data["income_statement"]
    pl["D13"] = float(isd["interest_on_portfolio"])
    pl["D14"] = float(isd["fees_and_commissions"])
    pl["D15"] = 0
    pl["D16"] = 0
    pl["D17"] = float(isd["other_operating_revenue"])
    for cell in ("D20", "D21", "D22", "D23", "D24"):
        pl[cell] = 0
    pl["D27"] = float(isd["provision_for_impairment"])
    pl["D28"] = -float(isd["loans_recovered"])  # recoveries reduce the charge
    pl["D30"] = float(isd["personnel_expense"])
    pl["D31"] = 0
    pl["D32"] = float(isd["other_admin_expense"])
    pl["D35"] = 0
    pl["D36"] = 0
    pl["D38"] = 0

    bs = wb["Balance Sheet"]
    bsd = data["balance_sheet"]
    bs["D13"] = float(bsd["cash_and_bank"])
    bs["D14"] = 0
    bs["D15"] = 0
    bs["D16"] = float(bsd["gross_loan_portfolio"])
    bs["D19"] = 0
    bs["D20"] = 0
    bs["D21"] = float(bsd["fixed_assets"])
    bs["D22"] = 0
    bs["D26"] = 0   # security deposits: microcredit enterprises take none
    bs["D27"] = float(bsd["borrowings"])
    bs["D28"] = 0
    bs["D30"] = 0
    bs["D31"] = float(bsd["payables"])
    bs["D32"] = 0
    bs["D35"] = float(bsd["paid_up_capital"])
    bs["D36"] = 0
    bs["D37"] = 0
    bs["D38"] = 0
    bs["D39"] = float(bsd["income_surplus_prior"])
    bs["D41"] = 0
    # Period averages (approximated with end-of-period figures)
    bs["D46"] = float(bsd["total_assets"])
    bs["D47"] = float(bsd["gross_loan_portfolio"])
    bs["D48"] = float(D(bsd["paid_up_capital"]) + D(bsd["income_surplus_prior"]))
    bs["D49"] = 0
    bs["D50"] = 0
    bs["D51"] = float(bsd["borrowings"])
    bs["D52"] = data["outreach"]["active_borrowers"]

    # Portfolio Report: actual provision per bucket (H17..H26)
    pr = wb["Portfolio Report"]
    p = data["portfolio"]
    bucket_rows = ["current", "d1_30", "d31_60", "d61_90", "d91_120",
                   "d121_150", "d151_180", "d181_365", "d365_plus"]
    for i, key in enumerate(bucket_rows):
        pr[f"H{17 + i}"] = float(D(p["ageing"][key]["actual_prov"]))
    pr["H26"] = float(D(p["rescheduled"]["value"]))  # rescheduled provisioned 100%
    pr["E15"] = float(D(p["top10_value"]))

    # Regional Breakdown: inputs in F (number) / G (value)
    rb = wb["Regional Breakdown "]
    rb["F14"] = p["loans_disbursed_count"]
    rb["G14"] = float(D(p["loans_disbursed_value"]))
    rb["F15"] = p["loans_outstanding_count"]
    rb["G15"] = float(D(p["loans_outstanding_value"]))
    rb["F16"] = p["written_off_count"]
    rb["G16"] = float(D(p["written_off_value"]))
    for i, key in enumerate(bucket_rows):
        rb[f"F{18 + i}"] = p["ageing"][key]["count"]
        rb[f"G{18 + i}"] = float(D(p["ageing"][key]["value"]))
    rb["F27"] = p["rescheduled"]["count"]
    rb["G27"] = float(D(p["rescheduled"]["value"]))

    # Sector rows 39..51 in template order
    for i, sector in enumerate(LOAN_PURPOSE_SECTORS):
        s = data["sectors"][sector]
        rb[f"F{39 + i}"] = s["count"]
        rb[f"G{39 + i}"] = float(D(s["value"]))

    o = data["outreach"]
    rb["E55"] = o["total_clients"]
    rb["E56"] = o["female_clients"]
    rb["E57"] = o["active_borrowers"]
    rb["E58"] = o["active_female_borrowers"]
    rb["E61"] = 0  # client investments: not permitted for microcredit
    rb["E62"] = 0
    rb["E63"] = 0
    rb["E64"] = o["youth_with_loans"]
    rb["E65"] = float(D(o["youth_loans_value"]))
    rb["E66"] = float(D(o["loans_to_women_value"]))
    rb["E67"] = 0
    rb["E68"] = 0

    out = wb["Outreach & Social Performance"]
    out["D12"] = o["new_clients"]
    out["D13"] = o["new_female_clients"]
    out["D14"] = o["new_borrowers"]
    out["D15"] = o["new_female_borrowers"]
    st = data["staff"]
    out["D28"] = st["total"]
    out["D29"] = st["female"]
    out["D30"] = st["loan_officers"]
    out["D31"] = 0
    out["D32"] = 0
    out["D34"] = st["management"]
    out["D35"] = 0
    out["D36"] = 0
    out["D37"] = 0
    out["D38"] = 0
    out["D40"] = 0
    out["D41"] = o["new_borrowers"]
    out["D47"] = 1  # one approved office (no branches)
    out["D48"] = 0

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def serialize_data(data: dict) -> str:
    return json.dumps(data, default=str)
