"""MCAG Members Reporting Template generation and Excel export."""
from datetime import date
from decimal import Decimal

from mcag.extensions import db
from mcag.services.loan_service import record_repayment
from mcag.services.mcag_report import (
    build_return_data, export_to_excel, quarter_dates,
)
from tests.conftest import create_active_loan

D = Decimal


def current_quarter():
    today = date.today()
    quarter = f"Q{(today.month - 1) // 3 + 1}"
    return today.year, quarter


class TestReturnData:
    def test_portfolio_derives_from_ledger(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a)
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        portfolio = data["portfolio"]
        assert portfolio["loans_disbursed_count"] == 1
        assert D(portfolio["loans_disbursed_value"]) == calc["principal"]
        assert D(portfolio["loans_outstanding_value"]) == loan.principal_outstanding
        # ageing adds up to gross portfolio (validation requirement)
        ageing_total = sum(D(v["value"]) for v in portfolio["ageing"].values())
        assert ageing_total == D(portfolio["loans_outstanding_value"])

    def test_sector_distribution(self, app, tenant_a):
        create_active_loan(tenant_a)
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        assert data["sectors"]["Commerce / Trading"]["count"] == 1

    def test_income_uses_cash_received(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a)
        first = loan.instalments[0]
        record_repayment(loan, first.total_due, date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        assert D(data["income_statement"]["interest_on_portfolio"]) == first.interest_due
        assert D(data["income_statement"]["fees_and_commissions"]) == calc["total_fees"]

    def test_outreach_counts(self, app, tenant_a):
        create_active_loan(tenant_a)
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        assert data["outreach"]["total_clients"] == 1
        assert data["outreach"]["active_borrowers"] == 1
        assert data["outreach"]["active_female_borrowers"] == 1

    def test_validation_flags_empty_portfolio(self, app, tenant_a):
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        assert any("portfolio is zero" in v for v in data["validation"])


class TestExcelExport:
    def test_fills_official_template(self, app, tenant_a):
        import io
        import openpyxl

        loan, _ = create_active_loan(tenant_a)
        record_repayment(loan, loan.instalments[0].total_due, date.today(),
                         "cash", tenant_a["institution"],
                         tenant_a["users"]["accounts"])
        db.session.commit()
        year, quarter = current_quarter()
        data = build_return_data(tenant_a["institution"], year, quarter)
        content = export_to_excel(data)
        wb = openpyxl.load_workbook(io.BytesIO(content))
        assert "Cover Sheet" in wb.sheetnames
        cover = wb["Cover Sheet"]
        assert cover["C4"].value == "Alpha Micro-Credit Enterprise"
        assert cover["C8"].value == quarter
        rb = wb["Regional Breakdown "]
        assert rb["F15"].value == 1  # one loan outstanding
        assert float(rb["G15"].value) == float(loan.principal_outstanding)


class TestQuarterDates:
    def test_q1(self):
        start, end = quarter_dates(2026, "Q1")
        assert start == date(2026, 1, 1)
        assert end == date(2026, 3, 31)

    def test_q4(self):
        start, end = quarter_dates(2026, "Q4")
        assert end == date(2026, 12, 31)
