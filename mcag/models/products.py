"""Configurable loan products (Business, Personal, Contract, Funeral, Salary...)."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin


class LoanProduct(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "loan_products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    code = db.Column(db.String(20), nullable=False)
    description = db.Column(db.Text)

    min_amount = db.Column(db.Numeric(18, 2), nullable=False)
    max_amount = db.Column(db.Numeric(18, 2), nullable=False)
    min_tenure = db.Column(db.Integer, nullable=False)   # in repayment periods
    max_tenure = db.Column(db.Integer, nullable=False)
    repayment_frequency = db.Column(db.String(20), nullable=False, default="monthly")
    interest_method = db.Column(db.String(30), nullable=False, default="flat")
    schedule_type = db.Column(db.String(30), nullable=False, default="equal_instalment")
    min_rate = db.Column(db.Numeric(8, 4), nullable=False)   # % per period basis (annual)
    max_rate = db.Column(db.Numeric(8, 4), nullable=False)
    rate_period = db.Column(db.String(10), nullable=False, default="monthly")
    # monthly | annual — how the quoted rate is expressed

    application_fee = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    processing_fee_percent = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    processing_fee_fixed = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    other_fees = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    fees_deducted_upfront = db.Column(db.Boolean, nullable=False, default=True)

    # Penalty configuration (never hard-coded; warning thresholds apply)
    penalty_basis = db.Column(db.String(30), nullable=False, default="overdue_instalment")
    # overdue_instalment | overdue_principal
    penalty_rate_percent = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    penalty_fixed_amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    penalty_grace_days = db.Column(db.Integer, nullable=False, default=0)
    penalty_max_percent = db.Column(db.Numeric(8, 4))  # cap as % of instalment

    early_settlement_charge_percent = db.Column(db.Numeric(8, 4), nullable=False, default=0)
    early_settlement_terms = db.Column(db.Text)
    grace_periods = db.Column(db.Integer, nullable=False, default=0)

    guarantors_required = db.Column(db.Integer, nullable=False, default=1)
    collateral_required = db.Column(db.Boolean, nullable=False, default=False)
    required_documents = db.Column(db.Text)  # newline-separated document types
    approval_authority = db.Column(db.String(40), nullable=False, default="manager")

    active = db.Column(db.Boolean, nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("institution_id", "code", name="uq_product_code_per_institution"),
    )

    def required_documents_list(self):
        return [d.strip() for d in (self.required_documents or "").splitlines() if d.strip()]
