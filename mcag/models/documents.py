"""Secure document storage records."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin


class Document(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    # Random storage key — never a predictable URL.
    storage_key = db.Column(db.String(80), nullable=False, unique=True)
    original_filename = db.Column(db.String(255), nullable=False)
    document_type = db.Column(db.String(80), nullable=False, index=True)
    content_type = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer)
    sha256 = db.Column(db.String(64), index=True)

    # Ownership links (any may be null)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    loan_application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    guarantor_id = db.Column(db.Integer, db.ForeignKey("guarantors.id"), index=True)
    collateral_id = db.Column(db.Integer, db.ForeignKey("collaterals.id"), index=True)

    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verification_status = db.Column(db.String(20), nullable=False, default="pending")
    # pending | verified | rejected
    verified_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)
    expiry_date = db.Column(db.Date)
    immutable = db.Column(db.Boolean, nullable=False, default=False)
    # immutable=True for generated/issued documents (offer letters, agreements)

    uploaded_by = db.relationship("User", foreign_keys=[uploaded_by_id])
    verified_by = db.relationship("User", foreign_keys=[verified_by_id])
    customer = db.relationship("Customer", backref="documents")
