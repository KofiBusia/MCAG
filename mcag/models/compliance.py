"""Credit bureau register, MCAG returns, audit trail, alerts, data protection."""
from mcag.extensions import db
from mcag.models.base import TenantMixin, TimestampMixin, utcnow


class CreditBureauEnquiry(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "credit_bureau_enquiries"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"), index=True)
    consent_given = db.Column(db.Boolean, nullable=False, default=False)
    consent_date = db.Column(db.Date)
    bureau_name = db.Column(db.String(120), nullable=False)
    enquiry_date = db.Column(db.Date, nullable=False)
    report_reference = db.Column(db.String(120))
    report_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))
    existing_facilities = db.Column(db.Text)
    outstanding_balances = db.Column(db.Numeric(18, 2))
    arrears = db.Column(db.Numeric(18, 2))
    defaults_found = db.Column(db.Boolean, default=False)
    officer_comments = db.Column(db.Text)
    impact_on_decision = db.Column(db.String(255))
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer")
    officer = db.relationship("User")


class CreditBureauSubmission(TenantMixin, TimestampMixin, db.Model):
    """History of periodic data submissions to credit bureaus."""
    __tablename__ = "credit_bureau_submissions"

    id = db.Column(db.Integer, primary_key=True)
    bureau_name = db.Column(db.String(120), nullable=False)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    submitted_at = db.Column(db.DateTime)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    record_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), nullable=False, default="draft")
    # draft | exported | submitted | corrected | disputed
    correction_notes = db.Column(db.Text)
    dispute_status = db.Column(db.String(120))

    submitted_by = db.relationship("User")


class McagReturn(TenantMixin, TimestampMixin, db.Model):
    """A monthly MCAG Members Reporting Template return generated from the
    ledger and accounting records — totals are never typed by hand."""
    __tablename__ = "mcag_returns"

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    status = db.Column(db.String(20), nullable=False, default="draft")
    # draft | locked | submitted
    generated_at = db.Column(db.DateTime)
    generated_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    locked_at = db.Column(db.DateTime)
    locked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    submitted_at = db.Column(db.DateTime)
    submitted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    proof_document_id = db.Column(db.Integer, db.ForeignKey("documents.id"))
    data_json = db.Column(db.Text)          # exact version snapshot
    validation_json = db.Column(db.Text)    # validation results at generation

    generated_by = db.relationship("User", foreign_keys=[generated_by_id])
    submitted_by = db.relationship("User", foreign_keys=[submitted_by_id])

    __table_args__ = (
        db.UniqueConstraint("institution_id", "period", name="uq_mcag_return_period"),
    )


class AuditLog(db.Model):
    """Permanent, tamper-resistant audit trail. No update or delete routes
    exist for this table; ordinary administrators cannot remove entries."""
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    institution_id = db.Column(db.Integer, index=True)  # nullable: platform events
    user_id = db.Column(db.Integer, index=True)
    user_email = db.Column(db.String(255))
    occurred_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    action = db.Column(db.String(60), nullable=False, index=True)
    record_type = db.Column(db.String(60))
    record_id = db.Column(db.String(40))
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    ip_address = db.Column(db.String(64))
    session_info = db.Column(db.String(255))


class DuplicateAlert(TenantMixin, TimestampMixin, db.Model):
    """Fraud/duplicate detection alerts. Alerts require human review — the
    system never automatically accuses or declines a customer."""
    __tablename__ = "duplicate_alerts"

    id = db.Column(db.Integer, primary_key=True)
    alert_type = db.Column(db.String(60), nullable=False, index=True)
    severity = db.Column(db.String(10), nullable=False, default="medium")  # low|medium|high
    message = db.Column(db.Text, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    related_record_type = db.Column(db.String(60))
    related_record_id = db.Column(db.Integer)
    status = db.Column(db.String(20), nullable=False, default="open", index=True)
    # open | under_review | cleared | confirmed
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_notes = db.Column(db.Text)

    customer = db.relationship("Customer")
    reviewed_by = db.relationship("User")


class ConsentRecord(TenantMixin, TimestampMixin, db.Model):
    """Data protection consent register."""
    __tablename__ = "consent_records"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    consent_type = db.Column(db.String(60), nullable=False)
    # data_processing | credit_bureau | gps_capture | photograph
    given = db.Column(db.Boolean, nullable=False, default=True)
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    notes = db.Column(db.Text)

    customer = db.relationship("Customer")


class DataRequest(TenantMixin, TimestampMixin, db.Model):
    """Data subject access / correction requests."""
    __tablename__ = "data_requests"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    request_type = db.Column(db.String(30), nullable=False)  # access | correction | deletion
    details = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="open")
    # open | in_progress | completed | declined
    resolution = db.Column(db.Text)
    resolved_at = db.Column(db.DateTime)
    handled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    customer = db.relationship("Customer")


class DataBreachRecord(TenantMixin, TimestampMixin, db.Model):
    __tablename__ = "data_breach_records"

    id = db.Column(db.Integer, primary_key=True)
    occurred_on = db.Column(db.Date, nullable=False)
    discovered_on = db.Column(db.Date, nullable=False)
    description = db.Column(db.Text, nullable=False)
    data_affected = db.Column(db.Text)
    persons_affected = db.Column(db.Integer)
    containment_actions = db.Column(db.Text)
    reported_to_dpc = db.Column(db.Boolean, nullable=False, default=False)
    dpc_report_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="open")
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
