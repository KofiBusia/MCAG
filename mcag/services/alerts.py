"""Duplicate and fraud detection. Alerts flag records for human review —
the system never automatically accuses or declines a customer."""
from mcag.constants import ALERT_TYPES, LOAN_ACTIVE, LOAN_WRITTEN_OFF
from mcag.extensions import db
from mcag.models import (
    Customer, Document, DuplicateAlert, GuaranteeLink, Guarantor, Loan, User,
)
from mcag.utils import normalize_phone


def _raise_alert(institution_id, alert_type, message, customer=None,
                 related_type=None, related_id=None, severity="medium"):
    exists = DuplicateAlert.query.filter_by(
        institution_id=institution_id, alert_type=alert_type,
        customer_id=customer.id if customer else None,
        related_record_type=related_type, related_record_id=related_id,
        status="open",
    ).first()
    if exists:
        return exists
    alert = DuplicateAlert(
        institution_id=institution_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        customer_id=customer.id if customer else None,
        related_record_type=related_type,
        related_record_id=related_id,
    )
    db.session.add(alert)
    return alert


def scan_customer(customer: Customer) -> list:
    """Run duplicate/fraud checks for a customer. Returns created alerts."""
    inst_id = customer.institution_id
    alerts = []

    def others(query):
        return query.filter(Customer.institution_id == inst_id,
                            Customer.id != customer.id)

    if customer.ghana_card_number:
        dup = others(Customer.query.filter(
            Customer.ghana_card_number == customer.ghana_card_number)).first()
        if dup:
            alerts.append(_raise_alert(
                inst_id, "duplicate_ghana_card",
                f"Ghana Card also appears on customer {dup.customer_number} ({dup.full_name}).",
                customer, "Customer", dup.id, "high"))

    phone = normalize_phone(customer.phone_primary)
    if phone:
        dup = others(Customer.query.filter(Customer.phone_primary == customer.phone_primary)).first()
        if dup:
            alerts.append(_raise_alert(
                inst_id, "duplicate_phone",
                f"Telephone number also appears on customer {dup.customer_number}.",
                customer, "Customer", dup.id))
        staff = User.query.filter(User.institution_id == inst_id,
                                  User.phone == customer.phone_primary).first()
        if staff:
            alerts.append(_raise_alert(
                inst_id, "staff_phone_match",
                f"Customer phone matches staff member {staff.full_name}.",
                customer, "User", staff.id, "high"))

    if customer.bank_account_number:
        dup = others(Customer.query.filter(
            Customer.bank_account_number == customer.bank_account_number)).first()
        if dup:
            alerts.append(_raise_alert(
                inst_id, "duplicate_bank_account",
                f"Bank account also appears on customer {dup.customer_number}.",
                customer, "Customer", dup.id, "high"))

    if customer.momo_number:
        dup = others(Customer.query.filter(Customer.momo_number == customer.momo_number)).first()
        if dup:
            alerts.append(_raise_alert(
                inst_id, "duplicate_momo",
                f"Mobile money number also appears on customer {dup.customer_number}.",
                customer, "Customer", dup.id))

    # Written-off history
    if Loan.query.filter(Loan.institution_id == inst_id,
                         Loan.customer_id == customer.id,
                         Loan.status == LOAN_WRITTEN_OFF).count():
        alerts.append(_raise_alert(
            inst_id, "written_off_history",
            "Customer has a previously written-off loan.", customer, severity="high"))

    # Acting as guarantor
    if customer.ghana_card_number:
        g = Guarantor.query.filter(
            Guarantor.institution_id == inst_id,
            Guarantor.ghana_card_number == customer.ghana_card_number).first()
        if g:
            alerts.append(_raise_alert(
                inst_id, "customer_is_guarantor",
                "Customer is already acting as a guarantor.", customer, "Guarantor", g.id))
    return [a for a in alerts if a]


def scan_application(application) -> list:
    """Checks at application time (existing active loan, etc.)."""
    inst_id = application.institution_id
    alerts = []
    active = Loan.query.filter(
        Loan.institution_id == inst_id,
        Loan.customer_id == application.customer_id,
        Loan.status == LOAN_ACTIVE).first()
    if active:
        alerts.append(_raise_alert(
            inst_id, "existing_active_loan",
            f"Customer already has active loan {active.loan_number}.",
            application.customer, "Loan", active.id, "high"))
    return [a for a in alerts if a]


def scan_guarantor(guarantor: Guarantor) -> list:
    inst_id = guarantor.institution_id
    alerts = []
    links = GuaranteeLink.query.filter(
        GuaranteeLink.institution_id == inst_id,
        GuaranteeLink.guarantor_id == guarantor.id,
        GuaranteeLink.status == "active").count()
    if links >= 3:
        alerts.append(_raise_alert(
            inst_id, "guarantor_many_borrowers",
            f"Guarantor is linked to {links} active borrowers.",
            related_type="Guarantor", related_id=guarantor.id, severity="high"))
    return [a for a in alerts if a]


def scan_document_hash(document: Document) -> list:
    alerts = []
    if document.sha256:
        dup = Document.query.filter(
            Document.institution_id == document.institution_id,
            Document.sha256 == document.sha256,
            Document.id != document.id).first()
        if dup:
            alerts.append(_raise_alert(
                document.institution_id, "duplicate_document",
                f"Identical file already uploaded as document #{dup.id} "
                f"({dup.document_type}).",
                related_type="Document", related_id=dup.id))
    return [a for a in alerts if a]


def scan_disbursement_timing(loan) -> list:
    """Loan disbursed immediately after customer creation."""
    alerts = []
    customer = loan.customer
    if customer and loan.disbursed_at and customer.created_at:
        delta = loan.disbursed_at - customer.created_at
        if delta.days < 1:
            alerts.append(_raise_alert(
                loan.institution_id, "fast_disbursement",
                "Loan disbursed less than 24 hours after customer creation.",
                customer, "Loan", loan.id, "high"))
    return [a for a in alerts if a]
