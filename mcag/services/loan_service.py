"""Loan lifecycle service: creation, disbursement, repayment allocation,
reversal, penalties, waivers, write-off. Keeps the loan ledger, instalment
schedule, running balances and accounting journals in sync."""
from datetime import date
from decimal import Decimal

from mcag.constants import (
    INST_DUE, INST_NOT_DUE, INST_OVERDUE, INST_PAID, INST_PARTLY_PAID,
    INST_WRITTEN_OFF, LOAN_ACTIVE, LOAN_CLOSED, LOAN_WRITTEN_OFF,
)
from mcag.extensions import db
from mcag.models import (
    Disbursement, LedgerEntry, Loan, Repayment, ScheduleInstalment,
)
from mcag.services.accounting import post_journal
from mcag.services.audit import log_action
from mcag.utils import D, money

ZERO = Decimal("0.00")


class LoanServiceError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Loan creation
# ---------------------------------------------------------------------------
def create_loan_from_application(application, calc: dict, institution, user) -> Loan:
    """Create a loan and its locked schedule from an engine calculation."""
    seq = institution.take_sequence("next_loan_seq")
    loan = Loan(
        institution_id=institution.id,
        loan_number=f"LN-{seq:06d}",
        application_id=application.id,
        customer_id=application.customer_id,
        product_id=application.product_id,
        officer_id=application.created_by_id,
        principal=calc["principal"],
        interest_rate=calc["rate_percent"],
        rate_period=calc["rate_period"],
        interest_method=calc["interest_method"],
        schedule_type=calc["schedule_type"],
        repayment_frequency=calc["frequency"],
        tenure=calc["tenure"],
        grace_periods=calc["grace_periods"],
        total_interest=calc["total_interest"],
        total_fees=calc["total_fees"],
        total_repayment=calc["total_repayment"],
        apr=calc["apr"],
        first_due_date=calc["first_due_date"],
        final_due_date=calc["final_due_date"],
        status="Pending Disbursement",
        principal_outstanding=ZERO,
        interest_outstanding=ZERO,
        purpose_sector=application.purpose_sector,
        collection_zone_id=application.customer.collection_zone_id,
    )
    db.session.add(loan)
    db.session.flush()
    for row in calc["instalments"]:
        db.session.add(ScheduleInstalment(
            institution_id=institution.id,
            loan_id=loan.id,
            number=row["number"],
            due_date=row["due_date"],
            opening_principal=row["opening_principal"],
            principal_due=row["principal_due"],
            interest_due=row["interest_due"],
            fees_due=row.get("fees_due", ZERO),
            total_due=row["total_due"],
            closing_principal=row["closing_principal"],
            status=INST_NOT_DUE,
        ))
    log_action("loan_created", "Loan", loan.id, new_value={
        "loan_number": loan.loan_number, "principal": str(loan.principal)})
    return loan


# ---------------------------------------------------------------------------
# Disbursement
# ---------------------------------------------------------------------------
def disbursement_checklist(application) -> list:
    """Pre-disbursement validation. Returns a list of (label, ok) checks."""
    from mcag.constants import APP_READY_DISBURSE, APP_DOCS_COMPLETED
    from mcag.models import CreditBureauEnquiry, Document, GuaranteeLink

    offer = next((o for o in application.offers if o.status == "accepted"), None)
    agreement = next((a for a in application.agreements if a.executed), None)
    customer = application.customer
    id_verified = any(
        d.document_type == "Ghana Card" and d.verification_status == "verified"
        for d in customer.documents)
    bureau_done = CreditBureauEnquiry.query.filter_by(
        institution_id=application.institution_id,
        application_id=application.id).count() > 0
    product = application.product
    guarantors_ok = True
    if product.guarantors_required:
        active = [g for g in application.guarantee_links if g.status == "active"]
        guarantors_ok = len(active) >= product.guarantors_required
    collateral_ok = True
    if product.collateral_required:
        collateral_ok = len(application.collaterals) > 0
    return [
        ("Application approved", application.status in (
            "Approved", "Approved With Conditions", "Offer Issued", "Offer Accepted",
            APP_DOCS_COMPLETED, APP_READY_DISBURSE)),
        ("Offer accepted", offer is not None),
        ("Loan agreement signed/executed", agreement is not None),
        ("Customer Ghana Card verified", id_verified),
        ("Credit bureau check recorded", bureau_done),
        (f"Guarantors complete ({product.guarantors_required} required)", guarantors_ok),
        ("Collateral documented", collateral_ok),
    ]


def complete_disbursement(loan: Loan, disbursement: Disbursement, institution, user):
    """Activate the loan, write the ledger and post accounting entries."""
    if disbursement.status == "completed":
        raise LoanServiceError("Disbursement already completed.")
    loan.status = LOAN_ACTIVE
    from mcag.models.base import utcnow
    loan.disbursed_at = utcnow()
    loan.principal_outstanding = loan.principal
    loan.interest_outstanding = loan.total_interest
    disbursement.status = "completed"

    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=disbursement.disbursement_date, entry_type="disbursement",
        description=f"Disbursement {disbursement.method}",
        debit=loan.principal, credit=ZERO,
        reference_id=disbursement.id, created_by_id=user.id,
    ))

    cash_subtype = "cash" if disbursement.method == "cash" else "bank"
    lines = [("portfolio", loan.principal, ZERO)]
    fees = D(disbursement.fees_deducted)
    net = D(disbursement.net_amount)
    lines.append((cash_subtype, ZERO, net))
    if fees > 0:
        lines.append(("fee_income", ZERO, fees))
        db.session.add(LedgerEntry(
            institution_id=institution.id, loan_id=loan.id,
            entry_date=disbursement.disbursement_date, entry_type="fee",
            description="Fees deducted at disbursement",
            debit=ZERO, credit=ZERO,
            reference_id=disbursement.id, created_by_id=user.id,
        ))
    if D(disbursement.fees_paid_separately) > 0:
        # Fees collected in cash separately
        lines.append((cash_subtype, D(disbursement.fees_paid_separately), ZERO))
        lines.append(("fee_income", ZERO, D(disbursement.fees_paid_separately)))
    post_journal(institution, disbursement.disbursement_date,
                 f"Loan disbursement {loan.loan_number}", lines,
                 source="disbursement", reference_id=disbursement.id, user=user)
    log_action("loan_disbursed", "Loan", loan.id, new_value={
        "net": str(disbursement.net_amount), "method": disbursement.method})


# ---------------------------------------------------------------------------
# Repayment
# ---------------------------------------------------------------------------
def record_repayment(loan: Loan, amount, paid_at: date, method: str, institution,
                     user, external_reference: str = "", zone_id=None) -> Repayment:
    """Allocate a payment: penalties -> interest -> fees -> principal,
    oldest instalment first. Updates schedule, balances, ledger, journals."""
    amount = money(amount)
    if amount <= 0:
        raise LoanServiceError("Payment amount must be greater than zero.")
    if loan.status not in (LOAN_ACTIVE, "Restructured"):
        raise LoanServiceError(f"Loan is not active (status: {loan.status}).")

    remaining = amount
    alloc = {"penalty": ZERO, "interest": ZERO, "fees": ZERO, "principal": ZERO}

    instalments = sorted(
        [i for i in loan.instalments if i.status != INST_WRITTEN_OFF],
        key=lambda i: i.number)
    for inst in instalments:
        if remaining <= 0:
            break
        for bucket, due_attr, paid_attr in (
            ("penalty", "penalties_due", "penalties_paid"),
            ("interest", "interest_due", "interest_paid"),
            ("fees", "fees_due", "fees_paid"),
            ("principal", "principal_due", "principal_paid"),
        ):
            if remaining <= 0:
                break
            due = D(getattr(inst, due_attr)) - D(getattr(inst, paid_attr))
            if due <= 0:
                continue
            take = min(due, remaining)
            setattr(inst, paid_attr, money(D(getattr(inst, paid_attr)) + take))
            alloc[bucket] = money(alloc[bucket] + take)
            remaining = money(remaining - take)
        _refresh_instalment_status(inst, paid_at)

    if remaining > 0:
        # Overpayment: apply to principal (early reduction) on last instalment
        last = instalments[-1] if instalments else None
        if last is None:
            raise LoanServiceError("Loan has no schedule.")
        alloc["principal"] = money(alloc["principal"] + remaining)
        last.principal_paid = money(D(last.principal_paid) + remaining)
        remaining = ZERO
        _refresh_instalment_status(last, paid_at)

    seq = institution.take_sequence("next_receipt_seq")
    repayment = Repayment(
        institution_id=institution.id,
        loan_id=loan.id,
        receipt_number=f"RCP-{seq:06d}",
        amount=amount, paid_at=paid_at, method=method,
        external_reference=external_reference,
        collector_id=user.id,
        collection_zone_id=zone_id or loan.collection_zone_id,
        allocated_principal=alloc["principal"],
        allocated_interest=alloc["interest"],
        allocated_penalties=alloc["penalty"],
        allocated_fees=alloc["fees"],
    )
    db.session.add(repayment)
    db.session.flush()

    loan.principal_outstanding = money(D(loan.principal_outstanding) - alloc["principal"])
    loan.interest_outstanding = money(D(loan.interest_outstanding) - alloc["interest"])
    loan.penalties_outstanding = money(D(loan.penalties_outstanding) - alloc["penalty"])

    for bucket, entry_type in (("principal", "repayment_principal"),
                               ("interest", "repayment_interest"),
                               ("penalty", "repayment_penalty"),
                               ("fees", "repayment_fee")):
        if alloc[bucket] > 0:
            db.session.add(LedgerEntry(
                institution_id=institution.id, loan_id=loan.id,
                entry_date=paid_at, entry_type=entry_type,
                description=f"Receipt {repayment.receipt_number}",
                debit=ZERO, credit=alloc[bucket],
                reference_id=repayment.id, created_by_id=user.id,
            ))

    cash_subtype = "cash" if method == "cash" else "bank"
    lines = [(cash_subtype, amount, ZERO)]
    if alloc["principal"] > 0:
        lines.append(("portfolio", ZERO, alloc["principal"]))
    if alloc["interest"] > 0:
        lines.append(("interest_income", ZERO, alloc["interest"]))
    if alloc["penalty"] > 0:
        lines.append(("penalty_income", ZERO, alloc["penalty"]))
    if alloc["fees"] > 0:
        lines.append(("fee_income", ZERO, alloc["fees"]))
    post_journal(institution, paid_at, f"Repayment {loan.loan_number} {repayment.receipt_number}",
                 lines, source="repayment", reference_id=repayment.id, user=user)

    if loan.total_outstanding <= 0:
        loan.status = LOAN_CLOSED
        from mcag.models.base import utcnow
        loan.closed_at = utcnow()

    log_action("repayment_recorded", "Repayment", repayment.id, new_value={
        "loan": loan.loan_number, "amount": str(amount),
        "receipt": repayment.receipt_number})
    return repayment


def _refresh_instalment_status(inst: ScheduleInstalment, as_of: date):
    outstanding = inst.total_outstanding
    if outstanding <= 0:
        inst.status = INST_PAID
        inst.paid_at = as_of
    elif inst.amount_paid > 0:
        inst.status = INST_PARTLY_PAID if inst.due_date >= as_of else INST_OVERDUE
    elif inst.due_date < as_of:
        inst.status = INST_OVERDUE
    elif inst.due_date == as_of:
        inst.status = INST_DUE
    else:
        inst.status = INST_NOT_DUE


def reverse_repayment(repayment: Repayment, reason: str, approver, institution):
    """Reverse a receipt with maker-checker control. The receipt row is
    never deleted; it is flagged reversed and contra entries are posted."""
    if repayment.reversed:
        raise LoanServiceError("Receipt has already been reversed.")
    if approver.id == repayment.collector_id:
        raise LoanServiceError("A user cannot approve the reversal of their own receipt.")

    loan = repayment.loan
    repayment.reversed = True
    repayment.reversal_reason = reason
    repayment.reversal_approved_by_id = approver.id
    from mcag.models.base import utcnow
    repayment.reversed_at = utcnow()

    # Un-apply allocations, newest instalments first
    remaining = {
        "penalties": D(repayment.allocated_penalties),
        "interest": D(repayment.allocated_interest),
        "fees": D(repayment.allocated_fees),
        "principal": D(repayment.allocated_principal),
    }
    for inst in sorted(loan.instalments, key=lambda i: -i.number):
        for bucket, paid_attr in (("penalties", "penalties_paid"), ("interest", "interest_paid"),
                                  ("fees", "fees_paid"), ("principal", "principal_paid")):
            if remaining[bucket] <= 0:
                continue
            paid = D(getattr(inst, paid_attr))
            take = min(paid, remaining[bucket])
            if take > 0:
                setattr(inst, paid_attr, money(paid - take))
                remaining[bucket] = money(remaining[bucket] - take)
        _refresh_instalment_status(inst, date.today())

    loan.principal_outstanding = money(D(loan.principal_outstanding) + D(repayment.allocated_principal))
    loan.interest_outstanding = money(D(loan.interest_outstanding) + D(repayment.allocated_interest))
    loan.penalties_outstanding = money(D(loan.penalties_outstanding) + D(repayment.allocated_penalties))
    if loan.status == LOAN_CLOSED:
        loan.status = LOAN_ACTIVE
        loan.closed_at = None

    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=date.today(), entry_type="reversal",
        description=f"Reversal of receipt {repayment.receipt_number}: {reason}"[:255],
        debit=D(repayment.amount), credit=ZERO,
        reference_id=repayment.id, created_by_id=approver.id,
    ))

    cash_subtype = "cash" if repayment.method == "cash" else "bank"
    lines = [(cash_subtype, ZERO, D(repayment.amount))]
    if D(repayment.allocated_principal) > 0:
        lines.append(("portfolio", D(repayment.allocated_principal), ZERO))
    if D(repayment.allocated_interest) > 0:
        lines.append(("interest_income", D(repayment.allocated_interest), ZERO))
    if D(repayment.allocated_penalties) > 0:
        lines.append(("penalty_income", D(repayment.allocated_penalties), ZERO))
    if D(repayment.allocated_fees) > 0:
        lines.append(("fee_income", D(repayment.allocated_fees), ZERO))
    post_journal(institution, date.today(),
                 f"Reversal of receipt {repayment.receipt_number}",
                 lines, source="reversal", reference_id=repayment.id, user=approver)
    log_action("repayment_reversed", "Repayment", repayment.id,
               old_value={"amount": str(repayment.amount)},
               new_value={"reason": reason})


# ---------------------------------------------------------------------------
# Penalties, waivers, write-off
# ---------------------------------------------------------------------------
def apply_penalty(inst: ScheduleInstalment, amount, reason: str, institution, user):
    amount = money(amount)
    if amount <= 0:
        raise LoanServiceError("Penalty must be positive.")
    inst.penalties_due = money(D(inst.penalties_due) + amount)
    loan = inst.loan
    loan.penalties_outstanding = money(D(loan.penalties_outstanding) + amount)
    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=date.today(), entry_type="penalty",
        description=reason[:255] or "Penalty applied",
        debit=amount, credit=ZERO, created_by_id=user.id,
    ))
    log_action("penalty_applied", "Loan", loan.id,
               new_value={"instalment": inst.number, "amount": str(amount)})


def approve_waiver(waiver, approver, institution):
    if approver.id == waiver.requested_by_id:
        raise LoanServiceError("A user cannot approve their own waiver.")
    if waiver.status != "pending":
        raise LoanServiceError("Waiver is not pending.")
    loan = waiver.loan
    amount = D(waiver.amount)
    if waiver.waiver_type == "penalty":
        available = D(loan.penalties_outstanding)
        if amount > available:
            raise LoanServiceError("Waiver exceeds outstanding penalties.")
        loan.penalties_outstanding = money(available - amount)
        remaining = amount
        for inst in sorted(loan.instalments, key=lambda i: i.number):
            out = D(inst.penalties_due) - D(inst.penalties_paid)
            take = min(out, remaining)
            if take > 0:
                inst.penalties_due = money(D(inst.penalties_due) - take)
                remaining = money(remaining - take)
            _refresh_instalment_status(inst, date.today())
    elif waiver.waiver_type == "interest":
        available = D(loan.interest_outstanding)
        if amount > available:
            raise LoanServiceError("Waiver exceeds outstanding interest.")
        loan.interest_outstanding = money(available - amount)
        remaining = amount
        for inst in sorted(loan.instalments, key=lambda i: -i.number):
            out = D(inst.interest_due) - D(inst.interest_paid)
            take = min(out, remaining)
            if take > 0:
                inst.interest_due = money(D(inst.interest_due) - take)
                inst.total_due = money(D(inst.total_due) - take)
                remaining = money(remaining - take)
            _refresh_instalment_status(inst, date.today())
        loan.total_interest = money(D(loan.total_interest) - amount)
        loan.total_repayment = money(D(loan.total_repayment) - amount)
    else:
        raise LoanServiceError("Unsupported waiver type.")

    waiver.status = "approved"
    waiver.approved_by_id = approver.id
    from mcag.models.base import utcnow
    waiver.approved_at = utcnow()
    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=date.today(), entry_type="waiver",
        description=f"{waiver.waiver_type} waiver: {waiver.reason}"[:255],
        debit=ZERO, credit=amount, reference_id=waiver.id,
        created_by_id=approver.id,
    ))
    if loan.total_outstanding <= 0 and loan.status == LOAN_ACTIVE:
        loan.status = LOAN_CLOSED
    log_action("waiver_approved", "Waiver", waiver.id,
               new_value={"type": waiver.waiver_type, "amount": str(amount)})


def write_off_loan(loan: Loan, reason: str, requester, approver, institution):
    if requester.id == approver.id:
        raise LoanServiceError("A user cannot approve their own write-off.")
    if loan.status == LOAN_WRITTEN_OFF:
        raise LoanServiceError("Loan already written off.")
    principal_out = D(loan.principal_outstanding)
    loan.status = LOAN_WRITTEN_OFF
    from mcag.models.base import utcnow
    loan.written_off_at = utcnow()
    loan.write_off_reason = reason
    loan.write_off_requested_by_id = requester.id
    loan.write_off_approved_by_id = approver.id
    for inst in loan.instalments:
        if inst.total_outstanding > 0:
            inst.status = INST_WRITTEN_OFF

    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=date.today(), entry_type="write_off",
        description=f"Write-off: {reason}"[:255],
        debit=ZERO, credit=loan.total_outstanding,
        created_by_id=approver.id,
    ))
    if principal_out > 0:
        post_journal(institution, date.today(),
                     f"Write-off {loan.loan_number}",
                     [("bad_debt", principal_out, ZERO),
                      ("portfolio", ZERO, principal_out)],
                     source="write_off", reference_id=loan.id, user=approver)
    log_action("loan_written_off", "Loan", loan.id,
               new_value={"reason": reason, "principal": str(principal_out)})


def record_recovery_after_write_off(loan: Loan, amount, paid_at: date, method: str,
                                    institution, user) -> None:
    amount = money(amount)
    if loan.status != LOAN_WRITTEN_OFF:
        raise LoanServiceError("Loan is not written off.")
    if amount <= 0:
        raise LoanServiceError("Amount must be positive.")
    db.session.add(LedgerEntry(
        institution_id=institution.id, loan_id=loan.id,
        entry_date=paid_at, entry_type="recovery",
        description="Recovery after write-off",
        debit=ZERO, credit=amount, created_by_id=user.id,
    ))
    cash_subtype = "cash" if method == "cash" else "bank"
    post_journal(institution, paid_at, f"Recovery {loan.loan_number}",
                 [(cash_subtype, amount, ZERO), ("recovery_income", ZERO, amount)],
                 source="recovery", reference_id=loan.id, user=user)
    log_action("recovery_recorded", "Loan", loan.id, new_value={"amount": str(amount)})
