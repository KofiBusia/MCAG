"""Recovery actions and complaints register. No borrower-shaming features."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin


class RecoveryAction(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "recovery_actions"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    action_type = db.Column(db.String(40), nullable=False)
    # call | visit | guarantor_contact | promise_to_pay | demand_letter |
    # final_demand | legal_referral | collateral_action
    action_date = db.Column(db.Date, nullable=False)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    notes = db.Column(db.Text)
    promised_amount = db.Column(db.Numeric(18, 2))
    promise_date = db.Column(db.Date)
    outcome = db.Column(db.String(255))

    loan = db.relationship("Loan", backref="recovery_actions")
    officer = db.relationship("User")


class Complaint(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "complaints"

    id = db.Column(db.Integer, primary_key=True)
    complaint_number = db.Column(db.String(30), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    complainant_name = db.Column(db.String(255))
    date_received = db.Column(db.Date, nullable=False)
    channel = db.Column(db.String(30), nullable=False)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    assigned_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    investigation_notes = db.Column(db.Text)
    resolution = db.Column(db.Text)
    date_resolved = db.Column(db.Date)
    customer_informed = db.Column(db.Boolean, nullable=False, default=False)
    escalated = db.Column(db.Boolean, nullable=False, default=False)
    escalation_details = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    # open | investigating | resolved | escalated

    customer = db.relationship("Customer")
    assigned_officer = db.relationship("User")

    __table_args__ = (
        db.UniqueConstraint("institution_id", "complaint_number", name="uq_complaint_number_per_institution"),
    )
