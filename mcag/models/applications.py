"""Loan applications, field verification, credit assessment, approvals, offers."""
from mcag.constants import APP_DRAFT
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin, utcnow


class LoanApplication(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "loan_applications"

    id = db.Column(db.Integer, primary_key=True)
    application_number = db.Column(db.String(30), nullable=False, index=True)
    application_date = db.Column(db.Date, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("loan_products.id"), nullable=False)

    loan_purpose = db.Column(db.String(255), nullable=False)
    purpose_sector = db.Column(db.String(80))
    amount_requested = db.Column(db.Numeric(18, 2), nullable=False)
    proposed_tenure = db.Column(db.Integer, nullable=False)
    repayment_frequency = db.Column(db.String(20), nullable=False)
    proposed_payment_method = db.Column(db.String(30))
    proposed_collateral = db.Column(db.Text)
    application_fee_paid = db.Column(db.Numeric(18, 2), default=0)
    receiving_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    declaration_accepted = db.Column(db.Boolean, nullable=False, default=False)
    signed_by = db.Column(db.String(20))  # signature | thumbprint
    date_signed = db.Column(db.Date)

    status = db.Column(db.String(40), nullable=False, default=APP_DRAFT, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Approval decision
    approved_amount = db.Column(db.Numeric(18, 2))
    approved_tenure = db.Column(db.Integer)
    approved_rate = db.Column(db.Numeric(8, 4))
    approval_conditions = db.Column(db.Text)
    reduction_reason = db.Column(db.Text)
    decline_reason = db.Column(db.Text)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    policy_exceptions = db.Column(db.Text)

    customer = db.relationship("Customer", backref="applications")
    product = db.relationship("LoanProduct")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    receiving_officer = db.relationship("User", foreign_keys=[receiving_officer_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    status_history = db.relationship(
        "ApplicationStatusHistory", backref="application",
        order_by="ApplicationStatusHistory.changed_at", lazy="dynamic",
    )

    __table_args__ = (
        db.UniqueConstraint("institution_id", "application_number", name="uq_app_number_per_institution"),
    )

    def set_status(self, new_status: str, user_id: int, note: str = ""):
        old = self.status
        self.status = new_status
        db.session.add(ApplicationStatusHistory(
            institution_id=self.institution_id,
            application_id=self.id,
            from_status=old, to_status=new_status,
            changed_by_id=user_id, note=note,
        ))


class ApplicationStatusHistory(TenantMixin, db.Model):
    __tablename__ = "application_status_history"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False, index=True)
    from_status = db.Column(db.String(40))
    to_status = db.Column(db.String(40), nullable=False)
    changed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    changed_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    note = db.Column(db.Text)

    changed_by = db.relationship("User")


class FieldVerification(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "field_verifications"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False, index=True)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    visit_date = db.Column(db.Date, nullable=False)
    visit_time = db.Column(db.String(10))
    residence_visited = db.Column(db.Boolean, default=False)
    business_visited = db.Column(db.Boolean, default=False)
    gps_consent = db.Column(db.Boolean, default=False)
    gps_location = db.Column(db.String(80))  # only stored when gps_consent True
    digital_address = db.Column(db.String(40))
    business_activity_observed = db.Column(db.Text)
    stock_observed = db.Column(db.Text)
    employees_observed = db.Column(db.Integer)
    estimated_sales = db.Column(db.Numeric(18, 2))
    estimated_expenses = db.Column(db.Numeric(18, 2))
    business_operating_days = db.Column(db.Integer)
    premises_status = db.Column(db.String(80))
    landlord_verification = db.Column(db.Text)
    residence_verification = db.Column(db.Text)
    collateral_sighted = db.Column(db.Boolean, default=False)
    officer_comments = db.Column(db.Text)
    recommended_amount = db.Column(db.Numeric(18, 2))
    recommended_tenure = db.Column(db.Integer)
    outcome = db.Column(db.String(30), nullable=False, default="pending")
    # pending | satisfactory | unsatisfactory

    application = db.relationship("LoanApplication", backref="field_verifications")
    officer = db.relationship("User")


class CreditAssessment(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "credit_assessments"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False, index=True)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assessment_date = db.Column(db.Date, nullable=False)

    # Cash-flow assessment
    daily_sales = db.Column(db.Numeric(18, 2))
    monthly_sales = db.Column(db.Numeric(18, 2))
    cost_of_sales = db.Column(db.Numeric(18, 2))
    operating_expenses = db.Column(db.Numeric(18, 2))
    household_expenses = db.Column(db.Numeric(18, 2))
    existing_loan_repayments = db.Column(db.Numeric(18, 2))
    other_commitments = db.Column(db.Numeric(18, 2))
    net_disposable_income = db.Column(db.Numeric(18, 2))
    proposed_instalment = db.Column(db.Numeric(18, 2))
    repayment_surplus = db.Column(db.Numeric(18, 2))
    instalment_to_income_percent = db.Column(db.Numeric(8, 2))
    debt_service_ratio_percent = db.Column(db.Numeric(8, 2))

    # Character assessment
    residence_stability = db.Column(db.String(255))
    years_in_business_confirmed = db.Column(db.Numeric(5, 1))
    previous_repayment_history = db.Column(db.Text)
    supplier_references = db.Column(db.Text)
    community_references = db.Column(db.Text)
    credit_bureau_history = db.Column(db.Text)
    information_accuracy = db.Column(db.String(255))
    officer_observations = db.Column(db.Text)

    # Business assessment
    working_capital = db.Column(db.Numeric(18, 2))
    stock_value = db.Column(db.Numeric(18, 2))
    business_assets = db.Column(db.Numeric(18, 2))
    business_liabilities = db.Column(db.Numeric(18, 2))
    profitability = db.Column(db.Numeric(18, 2))
    seasonality = db.Column(db.Text)
    business_risks = db.Column(db.Text)
    owner_contribution = db.Column(db.Numeric(18, 2))

    # Recommendation — a human authorised officer makes the final decision.
    amount_recommended = db.Column(db.Numeric(18, 2))
    tenure_recommended = db.Column(db.Integer)
    frequency_recommended = db.Column(db.String(20))
    rate_recommended = db.Column(db.Numeric(8, 4))
    conditions = db.Column(db.Text)
    risk_rating = db.Column(db.String(20))
    recommendation = db.Column(db.String(30))  # approve | decline | defer

    application = db.relationship("LoanApplication", backref="assessments")
    officer = db.relationship("User")


class OfferLetter(TenantMixin, TimestampMixin, db.Model):
    """Immutable record of every issued loan offer. Figures are locked at
    generation time from the calculation engine (never typed by staff)."""
    __tablename__ = "offer_letters"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False, index=True)
    offer_number = db.Column(db.String(30), nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    printed_at = db.Column(db.DateTime)
    offer_expiry_date = db.Column(db.Date, nullable=False)
    accepted_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    signed_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))
    status = db.Column(db.String(20), nullable=False, default="issued")
    # issued | accepted | rejected | expired | superseded

    # Locked calculation snapshot (JSON) — immutable once issued.
    calculation_json = db.Column(db.Text, nullable=False)

    application = db.relationship("LoanApplication", backref="offers")
    generated_by = db.relationship("User", foreign_keys=[generated_by_id])
    signed_document = db.relationship("Document", foreign_keys=[signed_document_id])


class LoanAgreement(TenantMixin, TimestampMixin, db.Model):
    """Immutable loan agreement record based on the MCAG template. The
    institution's exact legal name is inserted automatically in every
    section — different lender names can never appear."""
    __tablename__ = "loan_agreements"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offer_letters.id"))
    agreement_date = db.Column(db.Date, nullable=False)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    language_explained = db.Column(db.String(60), default="English")
    witness_name = db.Column(db.String(200))
    witness_phone = db.Column(db.String(30))
    executed = db.Column(db.Boolean, nullable=False, default=False)
    executed_at = db.Column(db.DateTime)
    signed_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))
    calculation_json = db.Column(db.Text, nullable=False)

    application = db.relationship("LoanApplication", backref="agreements")
    offer = db.relationship("OfferLetter")
    generated_by = db.relationship("User", foreign_keys=[generated_by_id])
