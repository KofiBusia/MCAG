"""Guarantor and collateral registers."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin
from mcag.utils import mask_ghana_card


class Guarantor(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "guarantors"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(255), nullable=False)
    relationship_to_borrower = db.Column(db.String(120))
    ghana_card_number = db.Column(db.String(30), index=True)
    ghana_card_expiry_date = db.Column(db.Date)
    date_of_birth = db.Column(db.Date)
    phone = db.Column(db.String(30), index=True)
    residence = db.Column(db.String(255))
    occupation = db.Column(db.String(120))
    employer_or_business = db.Column(db.String(200))
    monthly_income = db.Column(db.Numeric(18, 2))
    max_guaranteed_amount = db.Column(db.Numeric(18, 2))
    status = db.Column(db.String(20), nullable=False, default="active")
    # active | released | defaulted

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    @property
    def masked_ghana_card(self):
        return mask_ghana_card(self.ghana_card_number) if self.ghana_card_number else ""


class GuaranteeLink(TenantMixin, TimestampMixin, db.Model):
    """Links a guarantor to a specific loan application/loan."""
    __tablename__ = "guarantee_links"

    id = db.Column(db.Integer, primary_key=True)
    guarantor_id = db.Column(db.Integer, db.ForeignKey("guarantors.id"), nullable=False, index=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    guaranteed_amount = db.Column(db.Numeric(18, 2))
    date_signed = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="active")
    # active | discharged | called

    guarantor = db.relationship("Guarantor", backref="guarantees")
    application = db.relationship("LoanApplication", backref="guarantee_links")


class Collateral(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "collaterals"

    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)

    collateral_type = db.Column(db.String(40), nullable=False)
    description = db.Column(db.Text, nullable=False)
    owner_name = db.Column(db.String(255))
    owner_relationship = db.Column(db.String(120))
    location = db.Column(db.String(255))
    estimated_market_value = db.Column(db.Numeric(18, 2))
    forced_sale_value = db.Column(db.Numeric(18, 2))
    valuation_date = db.Column(db.Date)
    valuer = db.Column(db.String(200))
    proof_of_ownership = db.Column(db.String(255))
    existing_encumbrances = db.Column(db.Text)
    insurance_details = db.Column(db.String(255))
    registration_details = db.Column(db.String(255))
    collateral_registry_reference = db.Column(db.String(120))
    registration_date = db.Column(db.Date)
    release_date = db.Column(db.Date)
    enforcement_status = db.Column(db.String(30), nullable=False, default="none")
    # none | notice_issued | enforcement | realised | released

    customer = db.relationship("Customer")
    application = db.relationship("LoanApplication", backref="collaterals")
