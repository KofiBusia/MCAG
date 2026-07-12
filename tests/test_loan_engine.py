"""Loan calculation engine tests — Decimal precision, validation, and the
MCAG sample-document inconsistency prevention."""
from datetime import date
from decimal import Decimal

import pytest

from mcag.services.loan_engine import (
    LoanCalculationError, add_period, build_schedule, early_settlement_quote,
    periodic_rate, serialize_calculation, validate_schedule,
)

D = Decimal
START = date(2026, 1, 15)


def build(**overrides):
    params = dict(
        principal="2500", rate_percent="6", rate_period="monthly",
        interest_method="flat", schedule_type="equal_instalment",
        frequency="monthly", tenure=4, disbursement_date=START,
    )
    params.update(overrides)
    return build_schedule(**params)


class TestFlat:
    def test_totals_are_consistent(self):
        calc = build()
        assert calc["total_interest"] == D("600.00")  # 2500 * 6% * 4
        assert calc["total_repayment"] == D("3100.00")
        total = sum(r["total_due"] for r in calc["instalments"])
        assert total == calc["total_repayment"]

    def test_mcag_sample_error_is_impossible(self):
        """The sample offer letter said 13 weekly payments of GH¢43 totalling
        GH¢3,100 — which doesn't add up. The engine must always produce
        instalments that sum exactly to the total repayment."""
        calc = build(frequency="weekly", tenure=13,
                     rate_percent="1.3846")  # arbitrary weekly-ish rate
        total = sum(r["total_due"] for r in calc["instalments"])
        assert total == calc["total_repayment"]
        assert calc["instalments"][-1]["closing_principal"] == D("0.00")
        # 13 * 43 = 559 ≠ total; no schedule row invents figures
        assert all(r["total_due"] > 0 for r in calc["instalments"])

    def test_rounding_lands_in_final_instalment(self):
        calc = build(principal="1000", tenure=3, rate_percent="10")
        principals = [r["principal_due"] for r in calc["instalments"]]
        assert sum(principals) == D("1000.00")
        assert principals[0] == D("333.33")
        assert principals[-1] == D("333.34")


class TestReducing:
    def test_equal_instalment_zero_final_balance(self):
        calc = build(interest_method="reducing_balance", principal="10000",
                     tenure=12, rate_percent="24", rate_period="annual")
        assert calc["instalments"][-1]["closing_principal"] == D("0.00")
        assert sum(r["principal_due"] for r in calc["instalments"]) == D("10000.00")
        # annuity: near-equal instalments
        amounts = {r["total_due"] for r in calc["instalments"][:-1]}
        assert len(amounts) == 1

    def test_equal_principal(self):
        calc = build(interest_method="reducing_balance",
                     schedule_type="equal_principal", principal="1200",
                     tenure=4, rate_percent="12", rate_period="annual")
        principals = [r["principal_due"] for r in calc["instalments"]]
        assert principals == [D("300.00")] * 4
        # declining interest
        interests = [r["interest_due"] for r in calc["instalments"]]
        assert interests == sorted(interests, reverse=True)

    def test_bullet(self):
        calc = build(interest_method="reducing_balance",
                     schedule_type="bullet", principal="5000", tenure=3)
        assert calc["instalments"][0]["principal_due"] == D("0.00")
        assert calc["instalments"][-1]["principal_due"] == D("5000.00")


class TestFrequenciesAndDates:
    def test_weekly_dates(self):
        calc = build(frequency="weekly", tenure=4)
        dates = [r["due_date"] for r in calc["instalments"]]
        assert (dates[1] - dates[0]).days == 7

    def test_fortnightly(self):
        calc = build(frequency="fortnightly", tenure=4)
        dates = [r["due_date"] for r in calc["instalments"]]
        assert (dates[1] - dates[0]).days == 14

    def test_monthly_end_of_month(self):
        assert add_period(date(2026, 1, 31), "monthly") == date(2026, 2, 28)

    def test_tenure_matches_due_dates(self):
        calc = build(tenure=6)
        assert calc["number_of_instalments"] == 6
        assert calc["final_due_date"] == calc["instalments"][-1]["due_date"]


class TestGraceAndFees:
    def test_grace_periods_interest_only(self):
        calc = build(grace_periods=2)
        rows = calc["instalments"]
        assert len(rows) == 6
        assert rows[0]["principal_due"] == D("0.00")
        assert rows[1]["principal_due"] == D("0.00")
        assert sum(r["principal_due"] for r in rows) == D("2500.00")

    def test_fees_and_net_disbursement(self):
        calc = build(application_fee="20", processing_fee_percent="2")
        assert calc["application_fee"] == D("20.00")
        assert calc["processing_fee"] == D("50.00")
        assert calc["total_fees"] == D("70.00")
        assert calc["net_amount_received"] == D("2430.00")
        assert calc["total_cost_of_credit"] == calc["total_interest"] + calc["total_fees"]

    def test_apr_generated_and_reasonable(self):
        calc = build(application_fee="20", processing_fee_percent="2")
        assert calc["apr"] > 0
        # flat 6%/month with fees must exceed 72% nominal annual
        assert calc["apr"] > D("72")


class TestValidationGuards:
    def test_rejects_zero_principal(self):
        with pytest.raises(LoanCalculationError):
            build(principal="0")

    def test_rejects_zero_tenure(self):
        with pytest.raises(LoanCalculationError):
            build(tenure=0)

    def test_rejects_negative_rate(self):
        with pytest.raises(LoanCalculationError):
            build(rate_percent="-1")

    def test_rejects_unknown_frequency(self):
        with pytest.raises(LoanCalculationError):
            build(frequency="daily")

    def test_tampered_schedule_detected(self):
        calc = build()
        calc["instalments"][0]["total_due"] += D("1.00")
        with pytest.raises(LoanCalculationError):
            validate_schedule(calc)

    def test_serialization_roundtrip(self):
        calc = build()
        data = serialize_calculation(calc)
        assert data["total_repayment"] == "3100.00"
        assert len(data["instalments"]) == 4


class TestPeriodicRate:
    def test_monthly_to_weekly(self):
        rate = periodic_rate("6", "monthly", "weekly")
        assert rate == D("0.06") * 12 / 52

    def test_annual_to_monthly(self):
        assert periodic_rate("24", "annual", "monthly") == D("0.02")
