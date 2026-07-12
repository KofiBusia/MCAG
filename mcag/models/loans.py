"""Loans, schedules, disbursements, repayments, ledger, restructuring."""
from decimal import Decimal

from mcag.constants import LOAN_ACTIVE
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin, utcnow


class Loan(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    loan_number = db.Column(db.String(30), nullable=False, index=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("loan_products.id"), nullable=False)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Locked figures from the calculation engine
    principal = db.Column(db.Numeric(18, 2), nullable=False)
    interest_rate = db.Column(db.Numeric(8, 4), nullable=False)
    rate_period = db.Column(db.String(10), nullable=False, default="monthly")
    interest_method = db.Column(db.String(30), nullable=False)
    schedule_type = db.Column(db.String(30), nullable=False)
    repayment_frequency = db.Column(db.String(20), nullable=False)
    tenure = db.Column(db.Integer, nullable=False)
    grace_periods = db.Column(db.Integer, nullable=False, default=0)
    total_interest = db.Column(db.Numeric(18, 2), nullable=False)
    total_fees = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    total_repayment = db.Column(db.Numeric(18, 2), nullable=False)
    apr = db.Column(db.Numeric(8, 2))
    first_due_date = db.Column(db.Date)
    final_due_date = db.Column(db.Date)

    status = db.Column(db.String(20), nullable=False, default=LOAN_ACTIVE, index=True)
    # Active | Closed | Written Off | Restructured
    disbursed_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)
    written_off_at = db.Column(db.DateTime)
    write_off_reason = db.Column(db.Text)
    write_off_requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    write_off_approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Running balances (maintained by services; ledger is source of truth)
    principal_outstanding = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    interest_outstanding = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    penalties_outstanding = db.Column(db.Numeric(18, 2), nullable=False, default=0)

    restructured_from_id = db.Column(db.Integer, db.ForeignKey("loans.id"))
    purpose_sector = db.Column(db.String(80))
    collection_zone_id = db.Column(db.Integer, db.ForeignKey("collection_zones.id"))

    customer = db.relationship("Customer", backref="loans")
    product = db.relationship("LoanProduct")
    application = db.relationship("LoanApplication", backref="loan", uselist=False)
    officer = db.relationship("User", foreign_keys=[officer_id])
    collection_zone = db.relationship("CollectionZone")
    restructured_from = db.relationship("Loan", remote_side=[id])

    instalments = db.relationship(
        "ScheduleInstalment", backref="loan",
        order_by="ScheduleInstalment.number", lazy="selectin",
        cascade="all, delete-orphan",
    )
    ledger_entries = db.relationship(
        "LedgerEntry", backref="loan",
        order_by="LedgerEntry.entry_date, LedgerEntry.id", lazy="dynamic",
    )

    __table_args__ = (
        db.UniqueConstraint("institution_id", "loan_number", name="uq_loan_number_per_institution"),
    )

    @property
    def total_outstanding(self) -> Decimal:
        return (self.principal_outstanding or 0) + (self.interest_outstanding or 0) + (self.penalties_outstanding or 0)

    def days_overdue(self, as_of=None) -> int:
        from datetime import date
        as_of = as_of or date.today()
        overdue = [i for i in self.instalments
                   if i.due_date < as_of and i.total_outstanding > 0
                   and i.status not in ("Written Off", "Rescheduled")]
        if not overdue:
            return 0
        return (as_of - min(i.due_date for i in overdue)).days


class ScheduleInstalment(TenantMixin, db.Model):
    __tablename__ = "schedule_instalments"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    opening_principal = db.Column(db.Numeric(18, 2), nullable=False)
    principal_due = db.Column(db.Numeric(18, 2), nullable=False)
    interest_due = db.Column(db.Numeric(18, 2), nullable=False)
    fees_due = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    penalties_due = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    total_due = db.Column(db.Numeric(18, 2), nullable=False)
    principal_paid = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    interest_paid = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    fees_paid = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    penalties_paid = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    paid_at = db.Column(db.Date)
    closing_principal = db.Column(db.Numeric(18, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Not Yet Due")

    __table_args__ = (
        db.UniqueConstraint("loan_id", "number", name="uq_instalment_number_per_loan"),
    )

    @property
    def amount_paid(self) -> Decimal:
        return (self.principal_paid or 0) + (self.interest_paid or 0) + (self.fees_paid or 0) + (self.penalties_paid or 0)

    @property
    def total_outstanding(self) -> Decimal:
        return ((self.total_due or 0) + (self.penalties_due or 0)) - self.amount_paid

    def days_overdue(self, as_of=None) -> int:
        from datetime import date
        as_of = as_of or date.today()
        if self.due_date >= as_of or self.total_outstanding <= 0:
            return 0
        return (as_of - self.due_date).days


class Disbursement(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "disbursements"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    gross_principal = db.Column(db.Numeric(18, 2), nullable=False)
    fees_deducted = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    fees_paid_separately = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    net_amount = db.Column(db.Numeric(18, 2), nullable=False)
    disbursement_date = db.Column(db.Date, nullable=False)
    method = db.Column(db.String(30), nullable=False)
    bank_account = db.Column(db.String(120))
    momo_number = db.Column(db.String(30))
    cheque_details = db.Column(db.String(120))
    payment_reference = db.Column(db.String(120))
    initiated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    authorised_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    authorised_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="pending")
    # pending | authorised | completed
    evidence_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))

    loan = db.relationship("Loan", backref="disbursements")
    initiated_by = db.relationship("User", foreign_keys=[initiated_by_id])
    authorised_by = db.relationship("User", foreign_keys=[authorised_by_id])


class Repayment(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "repayments"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    receipt_number = db.Column(db.String(30), nullable=False, index=True)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    paid_at = db.Column(db.Date, nullable=False, index=True)
    method = db.Column(db.String(30), nullable=False)
    external_reference = db.Column(db.String(120))
    collector_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    collection_zone_id = db.Column(db.Integer, db.ForeignKey("collection_zones.id"))
    allocated_principal = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    allocated_interest = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    allocated_penalties = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    allocated_fees = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    reversed = db.Column(db.Boolean, nullable=False, default=False)
    reversal_reason = db.Column(db.Text)
    reversal_requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reversal_approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reversed_at = db.Column(db.DateTime)

    loan = db.relationship("Loan", backref="repayments")
    collector = db.relationship("User", foreign_keys=[collector_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "receipt_number", name="uq_receipt_number_per_institution"),
    )


class LedgerEntry(TenantMixin, db.Model):
    """Loan ledger — the source of truth for statements, arrears and reports."""
    __tablename__ = "ledger_entries"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    entry_date = db.Column(db.Date, nullable=False, index=True)
    entry_type = db.Column(db.String(40), nullable=False)
    # disbursement | principal_scheduled | interest_scheduled | fee | penalty |
    # repayment_principal | repayment_interest | repayment_penalty | repayment_fee |
    # reversal | waiver | restructure | write_off | recovery
    description = db.Column(db.String(255))
    debit = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    credit = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    reference_id = db.Column(db.Integer)  # repayment/disbursement id
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    created_by = db.relationship("User")


class LoanRestructure(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "loan_restructures"

    id = db.Column(db.Integer, primary_key=True)
    original_loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    new_loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    restructure_type = db.Column(db.String(30), nullable=False)
    # reschedule | extension | moratorium | refinance | top_up | settlement_arrangement
    reason = db.Column(db.Text, nullable=False)
    customer_request_reference = db.Column(db.String(255))
    old_balance = db.Column(db.Numeric(18, 2), nullable=False)
    new_terms = db.Column(db.Text)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="pending")
    # pending | approved | rejected
    credit_bureau_treatment = db.Column(db.String(120))

    original_loan = db.relationship("Loan", foreign_keys=[original_loan_id])
    new_loan = db.relationship("Loan", foreign_keys=[new_loan_id])


class SettlementQuote(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "settlement_quotes"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    quote_date = db.Column(db.Date, nullable=False)
    principal_outstanding = db.Column(db.Numeric(18, 2), nullable=False)
    interest_accrued = db.Column(db.Numeric(18, 2), nullable=False)
    early_settlement_charge = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    penalties = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    waivers = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    total_settlement = db.Column(db.Numeric(18, 2), nullable=False)
    valid_until = db.Column(db.Date, nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    # open | settled | expired

    loan = db.relationship("Loan", backref="settlement_quotes")


class Waiver(TenantMixin, TimestampMixin, db.Model):
    """Charge/penalty waivers with maker-checker control."""
    __tablename__ = "waivers"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    waiver_type = db.Column(db.String(20), nullable=False)  # penalty | interest | fee
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="pending")
    # pending | approved | rejected

    loan = db.relationship("Loan", backref="waivers")
    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
