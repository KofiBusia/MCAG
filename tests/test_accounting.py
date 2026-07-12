"""Accounting: balanced journals, automatic postings, statements."""
from datetime import date
from decimal import Decimal

import pytest

from mcag.extensions import db
from mcag.services.accounting import (
    AccountingError, balance_sheet, get_account, account_balance,
    income_statement, post_journal, trial_balance,
)
from mcag.services.loan_service import record_repayment
from tests.conftest import create_active_loan

D = Decimal


class TestJournals:
    def test_unbalanced_journal_rejected(self, app, tenant_a):
        inst = tenant_a["institution"]
        with pytest.raises(AccountingError):
            post_journal(inst, date.today(), "bad",
                         [("cash", D("100"), D("0")),
                          ("fee_income", D("0"), D("90"))])

    def test_balanced_journal_posts(self, app, tenant_a):
        inst = tenant_a["institution"]
        entry = post_journal(inst, date.today(), "capital injection",
                             [("bank", D("1000"), D("0")),
                              ("capital", D("0"), D("1000"))],
                             source="funding")
        db.session.commit()
        assert entry.journal_number.startswith("JRN-")
        assert entry.total_debits == entry.total_credits == D("1000.00")


class TestAutomaticPostings:
    def test_disbursement_journal(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a, principal="2500")
        inst = tenant_a["institution"]
        portfolio = account_balance(inst.id, get_account(inst.id, "portfolio"))
        assert portfolio == D("2500.00")
        # cash credited with net; fee income recognised
        fee_income = account_balance(inst.id, get_account(inst.id, "fee_income"))
        assert fee_income == calc["total_fees"]

    def test_repayment_journal_recognises_interest(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        first = loan.instalments[0]
        record_repayment(loan, first.total_due, date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        inst = tenant_a["institution"]
        interest_income = account_balance(
            inst.id, get_account(inst.id, "interest_income"))
        assert interest_income == first.interest_due
        portfolio = account_balance(inst.id, get_account(inst.id, "portfolio"))
        assert portfolio == D("2500.00") - first.principal_due

    def test_trial_balance_balances(self, app, tenant_a):
        loan, _ = create_active_loan(tenant_a)
        record_repayment(loan, D("100"), date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        rows = trial_balance(tenant_a["institution"].id)
        debits = sum(r["debit"] for r in rows)
        credits = sum(r["credit"] for r in rows)
        assert debits == credits

    def test_income_statement_and_balance_sheet(self, app, tenant_a):
        loan, calc = create_active_loan(tenant_a)
        first = loan.instalments[0]
        record_repayment(loan, first.total_due, date.today(), "cash",
                         tenant_a["institution"], tenant_a["users"]["accounts"])
        db.session.commit()
        inst_id = tenant_a["institution"].id
        income = income_statement(inst_id, date(2000, 1, 1), date.today())
        assert income["total_income"] == calc["total_fees"] + first.interest_due
        bs = balance_sheet(inst_id, to_date=date.today())
        assert bs["balanced"] is True
