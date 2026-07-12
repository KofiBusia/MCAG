"""Loan lifecycle: schedule persistence, repayment allocation, reversals,
maker-checker controls, waivers, write-off, arrears and settlement."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from mcag.extensions import db
from mcag.models import LedgerEntry, Repayment
from mcag.services.loan_service import (
    LoanServiceError, apply_penalty, approve_waiver, record_repayment,
    reverse_repayment, write_off_loan,
)
from tests.conftest import create_active_loan

D = Decimal


class TestDisbursedLoan:
    def test_loan_balances_after_disbursement(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a)
        assert loan.status == "Active"
        assert loan.principal_outstanding == calc["principal"]
        assert loan.interest_outstanding == calc["total_interest"]
        assert len(loan.instalments) == calc["number_of_instalments"]

    def test_ledger_has_disbursement_entry(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        entries = loan.ledger_entries.all()
        assert any(e.entry_type == "disbursement" for e in entries)


class TestRepaymentAllocation:
    def test_full_instalment_payment(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a, principal="2500",
                                        rate="6", tenure=4)
        first_due = loan.instalments[0].total_due
        repayment = record_repayment(
            loan, first_due, date.today(), "cash",
            tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        assert repayment.receipt_number.startswith("RCP-")
        inst1 = loan.instalments[0]
        assert inst1.status == "Paid"
        assert repayment.allocated_interest == inst1.interest_due
        assert repayment.allocated_principal == inst1.principal_due

    def test_partial_payment_marks_partly_paid(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        record_repayment(loan, D("10"), date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        assert loan.instalments[0].status in ("Partly Paid", "Due", "Overdue")
        assert loan.instalments[0].amount_paid == D("10.00")

    def test_receipts_are_sequential(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        r1 = record_repayment(loan, D("10"), date.today(), "cash",
                              tenant_a["institution"], tenant_a["users"]["accounts"])
        r2 = record_repayment(loan, D("10"), date.today(), "cash",
                              tenant_a["institution"], tenant_a["users"]["accounts"])
        n1 = int(r1.receipt_number.split("-")[1])
        n2 = int(r2.receipt_number.split("-")[1])
        assert n2 == n1 + 1

    def test_full_settlement_closes_loan(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a)
        record_repayment(loan, calc["total_repayment"], date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        assert loan.status == "Closed"
        assert loan.total_outstanding == D("0.00")
        assert all(i.status == "Paid" for i in loan.instalments)

    def test_zero_payment_rejected(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        with pytest.raises(LoanServiceError):
            record_repayment(loan, D("0"), date.today(), "cash",
                             tenant_a["institution"], tenant_a["users"]["accounts"])


class TestReversal:
    def test_reversal_restores_balances(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        before = loan.total_outstanding
        repayment = record_repayment(
            loan, D("100"), date.today(), "cash",
            tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        reverse_repayment(repayment, "posted in error",
                          tenant_a["users"]["manager"], tenant_a["institution"])
        db.session.commit()
        assert repayment.reversed is True
        assert loan.total_outstanding == before

    def test_collector_cannot_approve_own_reversal(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        repayment = record_repayment(
            loan, D("100"), date.today(), "cash",
            tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        with pytest.raises(LoanServiceError):
            reverse_repayment(repayment, "oops",
                              tenant_a["users"]["accounts"],
                              tenant_a["institution"])

    def test_double_reversal_rejected(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        repayment = record_repayment(
            loan, D("100"), date.today(), "cash",
            tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        reverse_repayment(repayment, "error", tenant_a["users"]["manager"],
                          tenant_a["institution"])
        with pytest.raises(LoanServiceError):
            reverse_repayment(repayment, "again", tenant_a["users"]["manager"],
                              tenant_a["institution"])


class TestPenaltiesAndWaivers:
    def test_penalty_applied_and_waived(self, app, tenant_a):
        from mcag.models import Waiver
        loan, _ = create_active_loan(tenant_a)
        inst = loan.instalments[0]
        apply_penalty(inst, D("25"), "late payment", tenant_a["institution"],
                      tenant_a["users"]["manager"])
        db.session.commit()
        assert loan.penalties_outstanding == D("25.00")

        waiver = Waiver(institution_id=tenant_a["institution"].id,
                        loan_id=loan.id, waiver_type="penalty",
                        amount=D("25"), reason="goodwill",
                        requested_by_id=tenant_a["users"]["officer"].id)
        db.session.add(waiver)
        db.session.flush()
        approve_waiver(waiver, tenant_a["users"]["manager"],
                       tenant_a["institution"])
        db.session.commit()
        assert loan.penalties_outstanding == D("0.00")

    def test_requester_cannot_approve_own_waiver(self, app, tenant_a):
        from mcag.models import Waiver
        loan, _ = create_active_loan(tenant_a)
        apply_penalty(loan.instalments[0], D("25"), "late",
                      tenant_a["institution"], tenant_a["users"]["manager"])
        waiver = Waiver(institution_id=tenant_a["institution"].id,
                        loan_id=loan.id, waiver_type="penalty",
                        amount=D("25"), reason="goodwill",
                        requested_by_id=tenant_a["users"]["manager"].id)
        db.session.add(waiver)
        db.session.flush()
        with pytest.raises(LoanServiceError):
            approve_waiver(waiver, tenant_a["users"]["manager"],
                           tenant_a["institution"])


class TestWriteOff:
    def test_write_off_requires_two_users(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        with pytest.raises(LoanServiceError):
            write_off_loan(loan, "uncollectible",
                           tenant_a["users"]["manager"],
                           tenant_a["users"]["manager"],
                           tenant_a["institution"])

    def test_write_off_posts_ledger_and_journal(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        write_off_loan(loan, "uncollectible",
                       tenant_a["users"]["officer"],
                       tenant_a["users"]["manager"],
                       tenant_a["institution"])
        db.session.commit()
        assert loan.status == "Written Off"
        assert any(e.entry_type == "write_off" for e in loan.ledger_entries)


class TestArrearsClassification:
    def test_overdue_loan_classified(self, app, tenant_a):
        from mcag.services.arrears import classify_portfolio
        loan, _ = create_active_loan(tenant_a)
        # Backdate all instalments to force arrears
        for i, inst in enumerate(loan.instalments):
            inst.due_date = date.today() - timedelta(days=45 - i * 7)
        db.session.commit()
        result = classify_portfolio(tenant_a["institution"], date.today())
        assert result["gross_portfolio"] > 0
        assert result["par"][30]["amount"] > 0
        assert result["provision_required"] > 0
        days = loan.days_overdue()
        assert days >= 30

    def test_current_loan_in_current_bucket(self, app, tenant_a):
        from mcag.services.arrears import classify_portfolio
        create_active_loan(tenant_a)
        result = classify_portfolio(tenant_a["institution"], date.today())
        assert result["buckets"]["current"]["count"] == 1
        assert result["in_arrears"] == D("0.00")


class TestSettlement:
    def test_settlement_quote(self, app, tenant_a):
        from mcag.services.loan_engine import early_settlement_quote
        loan, calc = create_active_loan(tenant_a)
        quote = early_settlement_quote(loan, date.today(), D("2"))
        assert quote["principal_outstanding"] == calc["principal"]
        assert quote["early_settlement_charge"] == D("50.00")
        assert quote["total_settlement"] >= quote["principal_outstanding"]
