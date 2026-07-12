"""Platform-level models: institutions (tenants), subscriptions, support."""
import json

from mcag.extensions import db
from mcag.models.base import TimestampMixin, utcnow


class Institution(TimestampMixin, db.Model):
    """A registered microcredit enterprise (tenant).

    Each enterprise has exactly one approved office profile. Collection
    zones (see CollectionZone) are field operational areas, NOT branches.
    """
    __tablename__ = "institutions"

    id = db.Column(db.Integer, primary_key=True)
    legal_name = db.Column(db.String(255), nullable=False)
    trading_name = db.Column(db.String(255))
    business_registration_number = db.Column(db.String(100))
    tin = db.Column(db.String(50))
    mcag_membership_number = db.Column(db.String(50))
    bog_licence_reference = db.Column(db.String(100))
    office_address = db.Column(db.Text)
    digital_address = db.Column(db.String(50))
    area_of_operation = db.Column(db.Text)
    phone_primary = db.Column(db.String(30))
    phone_secondary = db.Column(db.String(30))
    email = db.Column(db.String(255))
    logo_document_id = db.Column(db.Integer)
    proprietor_name = db.Column(db.String(255))
    manager_name = db.Column(db.String(255))
    date_operations_commenced = db.Column(db.Date)
    principal_bank = db.Column(db.String(255))
    bank_account_name = db.Column(db.String(255))
    bank_account_number = db.Column(db.String(80))
    dpc_registration = db.Column(db.String(120))
    credit_bureau_relationship = db.Column(db.String(255))
    auditor_name = db.Column(db.String(255))
    accountant_name = db.Column(db.String(255))
    regulatory_renewal_date = db.Column(db.Date)
    mcag_renewal_date = db.Column(db.Date)

    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    # pending | active | suspended | rejected
    status_reason = db.Column(db.Text)
    approved_at = db.Column(db.DateTime)

    # Institution-level configurable settings stored as JSON
    # (provisioning rates, penalty limits, sensitive-field policy, etc.)
    settings_json = db.Column(db.Text, nullable=False, default="{}")

    # Sequential counters for document numbering (per tenant)
    next_customer_seq = db.Column(db.Integer, nullable=False, default=0)
    next_application_seq = db.Column(db.Integer, nullable=False, default=0)
    next_loan_seq = db.Column(db.Integer, nullable=False, default=0)
    next_receipt_seq = db.Column(db.Integer, nullable=False, default=0)
    next_complaint_seq = db.Column(db.Integer, nullable=False, default=0)
    next_journal_seq = db.Column(db.Integer, nullable=False, default=0)

    users = db.relationship("User", back_populates="institution", lazy="dynamic")

    @property
    def settings(self) -> dict:
        try:
            return json.loads(self.settings_json or "{}")
        except ValueError:
            return {}

    def set_settings(self, data: dict):
        self.settings_json = json.dumps(data)

    def setting(self, key, default=None):
        return self.settings.get(key, default)

    @property
    def display_name(self):
        return self.trading_name or self.legal_name

    def take_sequence(self, field: str) -> int:
        """Atomically consume the next sequence number for a counter column."""
        current = getattr(self, field) or 0
        setattr(self, field, current + 1)
        return current + 1


class Subscription(TimestampMixin, db.Model):
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    plan_name = db.Column(db.String(80), nullable=False, default="Standard")
    amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="active")  # active | expired | cancelled
    notes = db.Column(db.Text)

    institution = db.relationship("Institution")


class SupportRequest(TimestampMixin, db.Model):
    __tablename__ = "support_requests"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), index=True)
    raised_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    subject = db.Column(db.String(255), nullable=False)
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")  # open | in_progress | resolved
    response = db.Column(db.Text)
    resolved_at = db.Column(db.DateTime)

    institution = db.relationship("Institution")
    raised_by = db.relationship("User", foreign_keys=[raised_by_id])


class GlobalDocumentTemplate(TimestampMixin, db.Model):
    """Platform-managed document templates (offer letter, agreement, letters)."""
    __tablename__ = "global_document_templates"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), nullable=False, unique=True)
    name = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    active = db.Column(db.Boolean, nullable=False, default=True)


class CollectionZone(TimestampMixin, db.Model):
    """A field operational area (market, route, zone). NOT a branch or office."""
    __tablename__ = "collection_zones"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey("institutions.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    zone_type = db.Column(db.String(30), nullable=False, default="zone")
    # zone | market | route | area
    description = db.Column(db.Text)
    assigned_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    active = db.Column(db.Boolean, nullable=False, default=True)

    institution = db.relationship("Institution")
    assigned_officer = db.relationship("User", foreign_keys=[assigned_officer_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "name", name="uq_zone_name_per_institution"),
    )
