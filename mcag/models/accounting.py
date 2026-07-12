"""Double-entry accounting, cashbook and funding register."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin, utcnow


class Account(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "accounts"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # asset|liability|equity|income|expense
    subtype = db.Column(db.String(40), nullable=False, index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("institution_id", "code", name="uq_account_code_per_institution"),
    )

    @property
    def is_debit_normal(self):
        return self.type in ("asset", "expense")


class JournalEntry(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "journal_entries"

    id = db.Column(db.Integer, primary_key=True)
    journal_number = db.Column(db.String(30), nullable=False)
    entry_date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False)
    source = db.Column(db.String(40), nullable=False, default="manual")
    # manual | disbursement | repayment | reversal | provision | write_off |
    # waiver | expense | funding | recovery
    reference_id = db.Column(db.Integer)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    posted_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reversed_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entries.id"))

    lines = db.relationship("JournalLine", backref="entry", cascade="all, delete-orphan", lazy="selectin")
    posted_by = db.relationship("User", foreign_keys=[posted_by_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "journal_number", name="uq_journal_number_per_institution"),
    )

    @property
    def total_debits(self):
        return sum((l.debit or 0) for l in self.lines)

    @property
    def total_credits(self):
        return sum((l.credit or 0) for l in self.lines)


class JournalLine(TenantMixin, db.Model):
    __tablename__ = "journal_lines"

    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entries.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False, index=True)
    debit = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    credit = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    memo = db.Column(db.String(255))

    account = db.relationship("Account")


class Expense(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    expense_date = db.Column(db.Date, nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounts.id"), nullable=False)
    paid_from_subtype = db.Column(db.String(20), nullable=False, default="cash")  # cash | bank
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    payee = db.Column(db.String(200))
    description = db.Column(db.String(255), nullable=False)
    reference = db.Column(db.String(120))
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("journal_entries.id"))

    account = db.relationship("Account")
    recorded_by = db.relationship("User")


class CashbookDay(TenantMixin, TimestampMixin, db.Model):
    """Daily cash reconciliation with supervisor approval."""
    __tablename__ = "cashbook_days"

    id = db.Column(db.Integer, primary_key=True)
    book_date = db.Column(db.Date, nullable=False, index=True)
    opening_balance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    cash_receipts = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    cash_disbursements = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    expenses = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    cash_handed_over = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    cash_banked = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    closing_balance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    physical_count = db.Column(db.Numeric(18, 2))
    variance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    prepared_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    status = db.Column(db.String(20), nullable=False, default="open")
    # open | submitted | approved

    prepared_by = db.relationship("User", foreign_keys=[prepared_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "book_date", name="uq_cashbook_day_per_institution"),
    )


class FundingSource(TenantMixin, TimestampMixin, db.Model):
    """Register of lawful lending capital. Customer deposits and public
    savings must never be recorded here."""
    __tablename__ = "funding_sources"

    id = db.Column(db.Integer, primary_key=True)
    source_type = db.Column(db.String(40), nullable=False)
    provider_name = db.Column(db.String(255), nullable=False)
    amount = db.Column(db.Numeric(18, 2), nullable=False)
    date_received = db.Column(db.Date, nullable=False)
    interest_rate = db.Column(db.Numeric(8, 4))
    tenure_months = db.Column(db.Integer)
    repayment_terms = db.Column(db.Text)
    security_given = db.Column(db.String(255))
    outstanding_balance = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    notes = db.Column(db.Text)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    recorded_by = db.relationship("User")
