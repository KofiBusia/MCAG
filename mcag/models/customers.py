"""Customer (borrower) records digitising the MCAG Loan Application Form.

Customers are data subjects only — they NEVER have login accounts.
"""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin
from mcag.utils import calculate_age, mask_ghana_card


class Customer(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    customer_number = db.Column(db.String(30), nullable=False, index=True)

    # Personal information
    full_name = db.Column(db.String(255), nullable=False, index=True)
    alias = db.Column(db.String(120))
    sex = db.Column(db.String(10))
    date_of_birth = db.Column(db.Date)
    place_of_birth = db.Column(db.String(120))
    nationality = db.Column(db.String(80), default="Ghanaian")
    home_town = db.Column(db.String(120))
    region = db.Column(db.String(60))
    ghana_card_number = db.Column(db.String(30), index=True)
    ghana_card_issue_date = db.Column(db.Date)
    ghana_card_expiry_date = db.Column(db.Date)
    marital_status = db.Column(db.String(20))
    dependants = db.Column(db.Integer)
    phone_primary = db.Column(db.String(30), index=True)
    phone_secondary = db.Column(db.String(30))
    cycle_number = db.Column(db.Integer, default=1)

    # Sensitive optional fields — disabled by default, excluded from scoring.
    # Only stored when the institution has explicitly enabled them with a
    # recorded reason (institution setting "sensitive_fields_enabled").
    ethnicity = db.Column(db.String(80))
    religion = db.Column(db.String(80))
    place_of_worship = db.Column(db.String(120))
    worship_location = db.Column(db.String(120))
    worship_leader = db.Column(db.String(120))

    # Residential information
    house_number = db.Column(db.String(60))
    residential_digital_address = db.Column(db.String(40))
    residential_location = db.Column(db.String(200))
    residential_landmark = db.Column(db.String(200))
    years_at_residence = db.Column(db.Numeric(5, 1))
    accommodation_status = db.Column(db.String(30))
    # owned | renting | family_house | employer_provided | other
    landlord_name = db.Column(db.String(200))
    rent_expiry_date = db.Column(db.Date)

    # Employment classification
    employment_type = db.Column(db.String(20), default="self_employed")
    # self_employed | salaried | both

    # Self-employed information
    business_name = db.Column(db.String(200))
    business_type = db.Column(db.String(120))
    business_location = db.Column(db.String(200))
    business_landmark = db.Column(db.String(200))
    years_in_business = db.Column(db.Numeric(5, 1))
    years_at_business_location = db.Column(db.Numeric(5, 1))
    premises_type = db.Column(db.String(60))
    premises_status = db.Column(db.String(60))
    estimated_daily_sales = db.Column(db.Numeric(18, 2))
    estimated_daily_expenses = db.Column(db.Numeric(18, 2))
    estimated_working_capital = db.Column(db.Numeric(18, 2))
    number_of_employees = db.Column(db.Integer)
    other_income = db.Column(db.Numeric(18, 2))

    # Salaried information
    employer_name = db.Column(db.String(200))
    employer_location = db.Column(db.String(200))
    employer_business_type = db.Column(db.String(120))
    position = db.Column(db.String(120))
    years_employed = db.Column(db.Numeric(5, 1))
    net_monthly_salary = db.Column(db.Numeric(18, 2))

    # Payment accounts
    bank_name = db.Column(db.String(120))
    bank_branch = db.Column(db.String(120))
    bank_account_name = db.Column(db.String(200))
    bank_account_number = db.Column(db.String(60), index=True)
    momo_provider = db.Column(db.String(40))
    momo_number = db.Column(db.String(30), index=True)
    momo_name = db.Column(db.String(200))

    # Household finances
    household_income = db.Column(db.Numeric(18, 2))
    household_expenses = db.Column(db.Numeric(18, 2))
    existing_monthly_repayments = db.Column(db.Numeric(18, 2))
    source_of_repayment = db.Column(db.String(255))
    existing_loans_details = db.Column(db.Text)

    # Family / references
    spouse_name = db.Column(db.String(200))
    spouse_phone = db.Column(db.String(30))
    spouse_occupation = db.Column(db.String(120))
    next_of_kin_name = db.Column(db.String(200))
    next_of_kin_relationship = db.Column(db.String(80))
    next_of_kin_phone = db.Column(db.String(30))
    next_of_kin_address = db.Column(db.String(255))
    relatives_info = db.Column(db.Text)
    references_info = db.Column(db.Text)

    collection_zone_id = db.Column(db.Integer, db.ForeignKey("collection_zones.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    status = db.Column(db.String(20), nullable=False, default="active")
    # active | inactive | blacklist_review

    collection_zone = db.relationship("CollectionZone")
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "customer_number", name="uq_customer_number_per_institution"),
        db.Index("ix_customers_inst_ghana_card", "institution_id", "ghana_card_number"),
    )

    @property
    def age(self):
        return calculate_age(self.date_of_birth)

    @property
    def masked_ghana_card(self):
        return mask_ghana_card(self.ghana_card_number) if self.ghana_card_number else ""
