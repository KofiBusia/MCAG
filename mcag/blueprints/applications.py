"""Loan application workflow: application → field verification → credit
assessment → approval → offer letter → loan agreement, plus guarantors and
collateral registers."""
import json
from datetime import date, timedelta

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template, request,
    url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import page_args, permission_required
from mcag.constants import (
    APP_APPROVED, APP_APPROVED_CONDITIONS, APP_CREDIT_ASSESSMENT,
    APP_DECLINED, APP_DEFERRED, APP_DRAFT, APP_FIELD_VERIFICATION,
    APP_OFFER_ACCEPTED, APP_OFFER_ISSUED, APP_RECOMMENDED, APP_SUBMITTED,
    APP_WITHDRAWN, APPLICATION_STATUSES, COLLATERAL_TYPES,
    LOAN_PURPOSE_SECTORS, P_APPROVE, P_ASSESS, P_CREATE, P_EDIT,
    P_FIELD_VERIFY, P_RECOMMEND, P_VIEW, PAYMENT_METHODS, RISK_RATINGS,
)
from mcag.extensions import db
from mcag.models import (
    Collateral, CreditAssessment, Customer, FieldVerification, GuaranteeLink,
    Guarantor, LoanAgreement, LoanApplication, LoanProduct, OfferLetter,
)
from mcag.models.base import utcnow
from mcag.services.alerts import scan_application, scan_guarantor
from mcag.services.audit import log_action
from mcag.services.loan_engine import (
    LoanCalculationError, build_schedule, serialize_calculation,
)
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D, money

bp = Blueprint("applications", __name__)

WORKFLOW_TRANSITIONS = {
    APP_DRAFT: [APP_SUBMITTED, APP_WITHDRAWN],
    APP_SUBMITTED: ["KYC Review", APP_WITHDRAWN],
    "KYC Review": ["Awaiting Documents", APP_FIELD_VERIFICATION, APP_WITHDRAWN],
    "Awaiting Documents": [APP_FIELD_VERIFICATION, "Expired", APP_WITHDRAWN],
    APP_FIELD_VERIFICATION: [APP_CREDIT_ASSESSMENT, APP_WITHDRAWN],
    APP_CREDIT_ASSESSMENT: [APP_RECOMMENDED, APP_WITHDRAWN],
    APP_RECOMMENDED: [APP_APPROVED, APP_APPROVED_CONDITIONS, APP_DEFERRED, APP_DECLINED],
    APP_DEFERRED: [APP_CREDIT_ASSESSMENT, APP_DECLINED],
}


def _get_application(app_id):
    return get_tenant_or_404(LoanApplication, app_id)


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
@bp.route("/")
@permission_required(P_VIEW)
def index():
    page, per_page = page_args()
    status = request.args.get("status")
    query = tenant_query(LoanApplication)
    if status:
        query = query.filter(LoanApplication.status == status)
    pagination = query.order_by(LoanApplication.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return render_template("applications/index.html", pagination=pagination,
                           statuses=APPLICATION_STATUSES, status=status)


@bp.route("/new", methods=["GET", "POST"])
@permission_required(P_CREATE)
def new():
    customers = tenant_query(Customer).order_by(Customer.full_name).all()
    products = tenant_query(LoanProduct).filter_by(active=True).all()
    if request.method == "POST":
        customer = get_tenant_or_404(Customer, request.form.get("customer_id", type=int))
        product = get_tenant_or_404(LoanProduct, request.form.get("product_id", type=int))
        amount = D(request.form.get("amount_requested") or 0)
        tenure = request.form.get("proposed_tenure", type=int) or 0
        errors = []
        if amount < D(product.min_amount) or amount > D(product.max_amount):
            errors.append(
                f"Amount must be between {product.min_amount} and {product.max_amount} "
                f"for {product.name}.")
        if tenure < product.min_tenure or tenure > product.max_tenure:
            errors.append(
                f"Tenure must be between {product.min_tenure} and "
                f"{product.max_tenure} periods for {product.name}.")
        if not request.form.get("declaration_accepted"):
            errors.append("The applicant declaration must be accepted.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("applications/form.html", customers=customers,
                                   products=products, sectors=LOAN_PURPOSE_SECTORS,
                                   payment_methods=PAYMENT_METHODS)
        inst = current_user.institution
        seq = inst.take_sequence("next_application_seq")
        application = LoanApplication(
            application_number=f"APP-{seq:06d}",
            application_date=date.today(),
            customer_id=customer.id,
            product_id=product.id,
            loan_purpose=request.form.get("loan_purpose") or "",
            purpose_sector=request.form.get("purpose_sector"),
            amount_requested=money(amount),
            proposed_tenure=tenure,
            repayment_frequency=product.repayment_frequency,
            proposed_payment_method=request.form.get("proposed_payment_method"),
            proposed_collateral=request.form.get("proposed_collateral"),
            application_fee_paid=D(request.form.get("application_fee_paid") or 0),
            receiving_officer_id=current_user.id,
            declaration_accepted=True,
            signed_by=request.form.get("signed_by") or "signature",
            date_signed=date.today(),
            created_by_id=current_user.id,
        )
        stamp_tenant(application)
        db.session.add(application)
        db.session.flush()
        application.set_status(APP_SUBMITTED, current_user.id, "Application created")
        alerts = scan_application(application)
        log_action("application_created", "LoanApplication", application.id,
                   new_value={"number": application.application_number,
                              "amount": str(amount)})
        db.session.commit()
        if alerts:
            flash(f"{len(alerts)} alert(s) raised for review (e.g. existing "
                  "active loan). Review before proceeding.", "warning")
        flash(f"Application {application.application_number} submitted.", "success")
        return redirect(url_for("applications.detail", app_id=application.id))
    return render_template("applications/form.html", customers=customers,
                           products=products, sectors=LOAN_PURPOSE_SECTORS,
                           payment_methods=PAYMENT_METHODS)


@bp.route("/<int:app_id>")
@permission_required(P_VIEW)
def detail(app_id):
    application = _get_application(app_id)
    transitions = WORKFLOW_TRANSITIONS.get(application.status, [])
    guarantors = tenant_query(Guarantor).order_by(Guarantor.full_name).all()
    from mcag.services.loan_service import disbursement_checklist
    checklist = disbursement_checklist(application)
    return render_template(
        "applications/detail.html", app=application, transitions=transitions,
        guarantors=guarantors, collateral_types=COLLATERAL_TYPES,
        risk_ratings=RISK_RATINGS, checklist=checklist)


@bp.route("/<int:app_id>/status", methods=["POST"])
@permission_required(P_EDIT)
def change_status(app_id):
    application = _get_application(app_id)
    new_status = request.form.get("status")
    allowed = WORKFLOW_TRANSITIONS.get(application.status, [])
    if new_status not in allowed:
        flash(f"Cannot move from {application.status} to {new_status}.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    if new_status in (APP_APPROVED, APP_APPROVED_CONDITIONS, APP_DECLINED, APP_DEFERRED):
        flash("Approval decisions must be made on the approval form.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    application.set_status(new_status, current_user.id, request.form.get("note") or "")
    log_action("application_status_changed", "LoanApplication", application.id,
               new_value=new_status)
    db.session.commit()
    flash(f"Application moved to {new_status}.", "success")
    return redirect(url_for("applications.detail", app_id=application.id))


# ---------------------------------------------------------------------------
# Field verification
# ---------------------------------------------------------------------------
@bp.route("/<int:app_id>/field-verification", methods=["GET", "POST"])
@permission_required(P_FIELD_VERIFY)
def field_verification(app_id):
    application = _get_application(app_id)
    if request.method == "POST":
        gps_consent = bool(request.form.get("gps_consent"))
        record = FieldVerification(
            application_id=application.id,
            officer_id=current_user.id,
            visit_date=date.fromisoformat(request.form.get("visit_date")
                                          or date.today().isoformat()),
            visit_time=request.form.get("visit_time"),
            residence_visited=bool(request.form.get("residence_visited")),
            business_visited=bool(request.form.get("business_visited")),
            gps_consent=gps_consent,
            gps_location=(request.form.get("gps_location") if gps_consent else None),
            digital_address=request.form.get("digital_address"),
            business_activity_observed=request.form.get("business_activity_observed"),
            stock_observed=request.form.get("stock_observed"),
            employees_observed=request.form.get("employees_observed", type=int),
            estimated_sales=request.form.get("estimated_sales") or None,
            estimated_expenses=request.form.get("estimated_expenses") or None,
            business_operating_days=request.form.get("business_operating_days", type=int),
            premises_status=request.form.get("premises_status"),
            landlord_verification=request.form.get("landlord_verification"),
            residence_verification=request.form.get("residence_verification"),
            collateral_sighted=bool(request.form.get("collateral_sighted")),
            officer_comments=request.form.get("officer_comments"),
            recommended_amount=request.form.get("recommended_amount") or None,
            recommended_tenure=request.form.get("recommended_tenure", type=int),
            outcome=request.form.get("outcome") or "pending",
        )
        stamp_tenant(record)
        db.session.add(record)
        if application.status in (APP_SUBMITTED, "KYC Review", "Awaiting Documents"):
            application.set_status(APP_FIELD_VERIFICATION, current_user.id,
                                   "Field verification recorded")
        log_action("field_verification_recorded", "FieldVerification", None,
                   new_value={"application": application.application_number,
                              "outcome": record.outcome})
        db.session.commit()
        flash("Field verification saved.", "success")
        return redirect(url_for("applications.detail", app_id=application.id))
    return render_template("applications/field_verification.html", app=application)


# ---------------------------------------------------------------------------
# Credit assessment
# ---------------------------------------------------------------------------
ASSESSMENT_NUMBERS = [
    "daily_sales", "monthly_sales", "cost_of_sales", "operating_expenses",
    "household_expenses", "existing_loan_repayments", "other_commitments",
    "proposed_instalment", "working_capital", "stock_value", "business_assets",
    "business_liabilities", "profitability", "owner_contribution",
    "amount_recommended", "rate_recommended", "years_in_business_confirmed",
]
ASSESSMENT_TEXT = [
    "residence_stability", "previous_repayment_history", "supplier_references",
    "community_references", "credit_bureau_history", "information_accuracy",
    "officer_observations", "seasonality", "business_risks", "conditions",
    "risk_rating", "recommendation", "frequency_recommended",
]


@bp.route("/<int:app_id>/assessment", methods=["GET", "POST"])
@permission_required(P_ASSESS)
def assessment(app_id):
    application = _get_application(app_id)
    if request.method == "POST":
        record = CreditAssessment(
            application_id=application.id,
            officer_id=current_user.id,
            assessment_date=date.today(),
            tenure_recommended=request.form.get("tenure_recommended", type=int),
        )
        for field in ASSESSMENT_NUMBERS:
            value = (request.form.get(field) or "").strip()
            setattr(record, field, value or None)
        for field in ASSESSMENT_TEXT:
            setattr(record, field, request.form.get(field) or None)

        # Derived figures are computed, never typed
        monthly_sales = D(record.monthly_sales or 0)
        outgoings = (D(record.cost_of_sales or 0) + D(record.operating_expenses or 0)
                     + D(record.household_expenses or 0)
                     + D(record.existing_loan_repayments or 0)
                     + D(record.other_commitments or 0))
        record.net_disposable_income = money(monthly_sales - outgoings)
        instalment = D(record.proposed_instalment or 0)
        record.repayment_surplus = money(D(record.net_disposable_income) - instalment)
        if record.net_disposable_income and D(record.net_disposable_income) > 0:
            record.instalment_to_income_percent = money(
                instalment / D(record.net_disposable_income) * 100)
            record.debt_service_ratio_percent = money(
                (instalment + D(record.existing_loan_repayments or 0))
                / D(record.net_disposable_income) * 100)
        stamp_tenant(record)
        db.session.add(record)
        if application.status == APP_FIELD_VERIFICATION:
            application.set_status(APP_CREDIT_ASSESSMENT, current_user.id,
                                   "Credit assessment recorded")
        if record.recommendation and current_user.can(P_RECOMMEND):
            application.set_status(APP_RECOMMENDED, current_user.id,
                                   f"Recommendation: {record.recommendation}")
        log_action("credit_assessment_recorded", "CreditAssessment", None,
                   new_value={"application": application.application_number,
                              "recommendation": record.recommendation})
        db.session.commit()
        flash("Credit assessment saved. A human authorised officer makes the "
              "final decision.", "success")
        return redirect(url_for("applications.detail", app_id=application.id))
    return render_template("applications/assessment.html", app=application,
                           risk_ratings=RISK_RATINGS)


# ---------------------------------------------------------------------------
# Approval (maker-checker: approver must differ from creator)
# ---------------------------------------------------------------------------
@bp.route("/<int:app_id>/approve", methods=["GET", "POST"])
@permission_required(P_APPROVE)
def approve(app_id):
    application = _get_application(app_id)
    if application.status not in (APP_RECOMMENDED, APP_DEFERRED, APP_CREDIT_ASSESSMENT):
        flash("Application is not ready for an approval decision.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    if application.created_by_id == current_user.id:
        flash("Maker-checker control: the officer who created an application "
              "cannot approve it.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    if request.method == "POST":
        decision = request.form.get("decision")
        product = application.product
        if decision in ("approve", "approve_conditions"):
            approved_amount = D(request.form.get("approved_amount") or 0)
            approved_tenure = request.form.get("approved_tenure", type=int) or 0
            approved_rate = D(request.form.get("approved_rate") or 0)
            errors = []
            if approved_amount <= 0:
                errors.append("Approved amount must be positive.")
            if approved_rate < D(product.min_rate) or approved_rate > D(product.max_rate):
                errors.append(
                    f"Rate must be within the product range "
                    f"{product.min_rate}%–{product.max_rate}%.")
            if approved_tenure < product.min_tenure or approved_tenure > product.max_tenure:
                errors.append("Approved tenure is outside the product range.")
            if (current_user.approval_limit is not None
                    and approved_amount > D(current_user.approval_limit)):
                errors.append(
                    f"Amount exceeds your approval authority limit of "
                    f"{current_user.approval_limit}. Escalate to a higher approver.")
            if errors:
                for e in errors:
                    flash(e, "danger")
                return redirect(url_for("applications.approve", app_id=application.id))
            application.approved_amount = money(approved_amount)
            application.approved_tenure = approved_tenure
            application.approved_rate = approved_rate
            application.approval_conditions = request.form.get("conditions")
            application.reduction_reason = request.form.get("reduction_reason")
            application.policy_exceptions = request.form.get("policy_exceptions")
            application.approved_by_id = current_user.id
            application.approved_at = utcnow()
            status = (APP_APPROVED_CONDITIONS if decision == "approve_conditions"
                      else APP_APPROVED)
            application.set_status(status, current_user.id, "Approved")
            log_action("application_approved", "LoanApplication", application.id,
                       new_value={"amount": str(approved_amount),
                                  "rate": str(approved_rate),
                                  "tenure": approved_tenure})
        elif decision == "defer":
            application.set_status(APP_DEFERRED, current_user.id,
                                   request.form.get("decline_reason") or "")
            log_action("application_deferred", "LoanApplication", application.id)
        elif decision == "decline":
            application.decline_reason = request.form.get("decline_reason")
            application.set_status(APP_DECLINED, current_user.id,
                                   application.decline_reason or "")
            log_action("application_declined", "LoanApplication", application.id,
                       new_value={"reason": application.decline_reason})
        else:
            flash("Unknown decision.", "danger")
            return redirect(url_for("applications.approve", app_id=application.id))
        db.session.commit()
        flash("Decision recorded.", "success")
        return redirect(url_for("applications.detail", app_id=application.id))
    latest_assessment = (tenant_query(CreditAssessment)
                         .filter_by(application_id=application.id)
                         .order_by(CreditAssessment.created_at.desc()).first())
    return render_template("applications/approve.html", app=application,
                           assessment=latest_assessment)


# ---------------------------------------------------------------------------
# Offer letter — figures locked from the calculation engine
# ---------------------------------------------------------------------------
def _build_offer_calc(application, disbursement_date=None):
    product = application.product
    return build_schedule(
        principal=application.approved_amount,
        rate_percent=application.approved_rate,
        rate_period=product.rate_period,
        interest_method=product.interest_method,
        schedule_type=product.schedule_type,
        frequency=product.repayment_frequency,
        tenure=application.approved_tenure,
        disbursement_date=disbursement_date or date.today(),
        grace_periods=product.grace_periods,
        application_fee=product.application_fee,
        processing_fee_percent=product.processing_fee_percent,
        processing_fee_fixed=product.processing_fee_fixed,
        other_fees=product.other_fees,
        fees_deducted_upfront=product.fees_deducted_upfront,
    )


@bp.route("/<int:app_id>/offer", methods=["POST"])
@permission_required(P_EDIT)
def generate_offer(app_id):
    application = _get_application(app_id)
    if application.status not in (APP_APPROVED, APP_APPROVED_CONDITIONS):
        flash("An offer can only be issued for an approved application.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    try:
        calc = _build_offer_calc(application)
    except LoanCalculationError as exc:
        flash(f"Calculation error: {exc}", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    for offer in application.offers:
        if offer.status == "issued":
            offer.status = "superseded"
    validity_days = int(request.form.get("validity_days") or 30)
    offer = OfferLetter(
        application_id=application.id,
        offer_number=f"{application.application_number}-OFF{len(application.offers) + 1}",
        generated_by_id=current_user.id,
        offer_expiry_date=date.today() + timedelta(days=validity_days),
        calculation_json=json.dumps(serialize_calculation(calc)),
    )
    stamp_tenant(offer)
    db.session.add(offer)
    application.set_status(APP_OFFER_ISSUED, current_user.id, "Offer letter issued")
    log_action("offer_generated", "OfferLetter", None,
               new_value={"application": application.application_number,
                          "total_repayment": str(calc["total_repayment"])})
    db.session.commit()
    flash("Offer letter generated with locked figures.", "success")
    return redirect(url_for("applications.offer_view", app_id=application.id,
                            offer_id=offer.id))


def _offer_context(offer):
    calc = json.loads(offer.calculation_json)
    return {
        "offer": offer,
        "app": offer.application,
        "customer": offer.application.customer,
        "institution": current_user.institution,
        "calc": calc,
    }


@bp.route("/<int:app_id>/offer/<int:offer_id>")
@permission_required(P_VIEW)
def offer_view(app_id, offer_id):
    offer = get_tenant_or_404(OfferLetter, offer_id)
    if offer.application_id != app_id:
        abort(404)
    return render_template("applications/offer.html", **_offer_context(offer))


@bp.route("/<int:app_id>/offer/<int:offer_id>.pdf")
@permission_required(P_VIEW)
def offer_pdf(app_id, offer_id):
    offer = get_tenant_or_404(OfferLetter, offer_id)
    if offer.application_id != app_id:
        abort(404)
    from mcag.services.pdf import render_pdf
    pdf = render_pdf("pdf/offer_letter.html", **_offer_context(offer))
    offer.printed_at = utcnow()
    log_action("document_download", "OfferLetter", offer.id,
               new_value={"file": "offer letter pdf"})
    db.session.commit()
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition":
            f"attachment; filename=LOAN - OFFER LETTER {offer.offer_number}.pdf"})


@bp.route("/<int:app_id>/offer/<int:offer_id>.docx")
@permission_required(P_VIEW)
def offer_docx(app_id, offer_id):
    offer = get_tenant_or_404(OfferLetter, offer_id)
    if offer.application_id != app_id:
        abort(404)
    from mcag.services.word_export import offer_letter_docx
    calc = json.loads(offer.calculation_json)
    data = offer_letter_docx(current_user.institution, offer=offer,
                             application=offer.application, calc=calc)
    log_action("document_download", "OfferLetter", offer.id,
               new_value={"file": "offer letter docx"})
    db.session.commit()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename=LOAN - OFFER LETTER {offer.offer_number}.docx"})


@bp.route("/blank-offer-letter.docx")
@permission_required(P_VIEW)
def blank_offer_docx():
    from mcag.services.word_export import offer_letter_docx
    data = offer_letter_docx(current_user.institution)
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=LOAN - OFFER LETTER.docx"})


@bp.route("/<int:app_id>/offer/<int:offer_id>/decision", methods=["POST"])
@permission_required(P_EDIT)
def offer_decision(app_id, offer_id):
    offer = get_tenant_or_404(OfferLetter, offer_id)
    application = offer.application
    if offer.application_id != app_id or offer.status != "issued":
        abort(404)
    decision = request.form.get("decision")
    if decision == "accept":
        if offer.offer_expiry_date < date.today():
            offer.status = "expired"
            flash("Offer has expired — generate a new offer.", "danger")
        else:
            offer.status = "accepted"
            offer.accepted_at = utcnow()
            application.set_status(APP_OFFER_ACCEPTED, current_user.id,
                                   "Borrower accepted offer (physical signature)")
            flash("Offer marked as accepted. Upload the signed copy.", "success")
    elif decision == "reject":
        offer.status = "rejected"
        offer.rejected_at = utcnow()
        flash("Offer marked as rejected.", "info")
    log_action("offer_decision", "OfferLetter", offer.id, new_value=offer.status)
    db.session.commit()
    return redirect(url_for("applications.detail", app_id=application.id))


@bp.route("/<int:app_id>/offer/<int:offer_id>/signed-upload", methods=["POST"])
@permission_required(P_EDIT)
def offer_signed_upload(app_id, offer_id):
    offer = get_tenant_or_404(OfferLetter, offer_id)
    if offer.application_id != app_id:
        abort(404)
    from mcag.services.documents import DocumentError, save_upload
    try:
        document = save_upload(
            request.files.get("file"), current_user.institution_id,
            "Signed Offer Letter", current_user,
            application_id=offer.application_id, immutable=True)
        offer.signed_document_id = document.id
        db.session.commit()
        flash("Signed offer letter stored.", "success")
    except DocumentError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("applications.detail", app_id=app_id))


# ---------------------------------------------------------------------------
# Loan agreement
# ---------------------------------------------------------------------------
@bp.route("/<int:app_id>/agreement", methods=["POST"])
@permission_required(P_EDIT)
def generate_agreement(app_id):
    application = _get_application(app_id)
    offer = next((o for o in application.offers if o.status == "accepted"), None)
    if offer is None:
        flash("The borrower must accept the offer before the agreement is "
              "executed.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    agreement = LoanAgreement(
        application_id=application.id,
        offer_id=offer.id,
        agreement_date=date.today(),
        generated_by_id=current_user.id,
        language_explained=request.form.get("language_explained") or "English",
        witness_name=request.form.get("witness_name"),
        witness_phone=request.form.get("witness_phone"),
        calculation_json=offer.calculation_json,  # same locked figures
    )
    stamp_tenant(agreement)
    db.session.add(agreement)
    log_action("agreement_generated", "LoanAgreement", None,
               new_value={"application": application.application_number})
    db.session.commit()
    flash("Loan agreement generated. The institution's exact legal name is "
          "used in every section.", "success")
    return redirect(url_for("applications.agreement_view",
                            app_id=application.id, agreement_id=agreement.id))


def _agreement_context(agreement):
    return {
        "agreement": agreement,
        "app": agreement.application,
        "customer": agreement.application.customer,
        "institution": current_user.institution,
        "calc": json.loads(agreement.calculation_json),
    }


@bp.route("/<int:app_id>/agreement/<int:agreement_id>")
@permission_required(P_VIEW)
def agreement_view(app_id, agreement_id):
    agreement = get_tenant_or_404(LoanAgreement, agreement_id)
    if agreement.application_id != app_id:
        abort(404)
    return render_template("applications/agreement.html",
                           **_agreement_context(agreement))


@bp.route("/<int:app_id>/agreement/<int:agreement_id>.pdf")
@permission_required(P_VIEW)
def agreement_pdf(app_id, agreement_id):
    agreement = get_tenant_or_404(LoanAgreement, agreement_id)
    if agreement.application_id != app_id:
        abort(404)
    from mcag.services.pdf import render_pdf
    pdf = render_pdf("pdf/loan_agreement.html", **_agreement_context(agreement))
    log_action("document_download", "LoanAgreement", agreement.id,
               new_value={"file": "loan agreement pdf"})
    db.session.commit()
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition":
            f"attachment; filename=LOAN AGREEMENT {agreement.application.application_number}.pdf"})


@bp.route("/<int:app_id>/agreement/<int:agreement_id>.docx")
@permission_required(P_VIEW)
def agreement_docx(app_id, agreement_id):
    agreement = get_tenant_or_404(LoanAgreement, agreement_id)
    if agreement.application_id != app_id:
        abort(404)
    from mcag.services.word_export import loan_agreement_docx
    data = loan_agreement_docx(
        current_user.institution, agreement=agreement,
        application=agreement.application,
        calc=json.loads(agreement.calculation_json))
    log_action("document_download", "LoanAgreement", agreement.id,
               new_value={"file": "loan agreement docx"})
    db.session.commit()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename=LOAN AGREEMENT {agreement.application.application_number}.docx"})


@bp.route("/blank-agreement.docx")
@permission_required(P_VIEW)
def blank_agreement_docx():
    from mcag.services.word_export import loan_agreement_docx
    data = loan_agreement_docx(current_user.institution)
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": "attachment; filename=LOAN AGREEMENT.docx"})


@bp.route("/<int:app_id>/agreement/<int:agreement_id>/execute", methods=["POST"])
@permission_required(P_EDIT)
def agreement_execute(app_id, agreement_id):
    agreement = get_tenant_or_404(LoanAgreement, agreement_id)
    if agreement.application_id != app_id:
        abort(404)
    from mcag.services.documents import DocumentError, save_upload
    file = request.files.get("file")
    if file and file.filename:
        try:
            document = save_upload(
                file, current_user.institution_id, "Signed Loan Agreement",
                current_user, application_id=agreement.application_id, immutable=True)
            agreement.signed_document_id = document.id
        except DocumentError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("applications.detail", app_id=app_id))
    agreement.executed = True
    agreement.executed_at = utcnow()
    agreement.application.set_status("Documentation Completed", current_user.id,
                                     "Loan agreement executed")
    log_action("agreement_executed", "LoanAgreement", agreement.id)
    db.session.commit()
    flash("Agreement executed and recorded immutably.", "success")
    return redirect(url_for("applications.detail", app_id=app_id))


# ---------------------------------------------------------------------------
# Guarantors
# ---------------------------------------------------------------------------
@bp.route("/guarantors", methods=["GET", "POST"])
@permission_required(P_VIEW)
def guarantors():
    if request.method == "POST":
        if not current_user.can(P_CREATE):
            abort(403)
        record = Guarantor(
            full_name=request.form.get("full_name") or "",
            relationship_to_borrower=request.form.get("relationship_to_borrower"),
            ghana_card_number=request.form.get("ghana_card_number"),
            date_of_birth=(date.fromisoformat(request.form["date_of_birth"])
                           if request.form.get("date_of_birth") else None),
            ghana_card_expiry_date=(date.fromisoformat(request.form["ghana_card_expiry_date"])
                                    if request.form.get("ghana_card_expiry_date") else None),
            phone=request.form.get("phone"),
            residence=request.form.get("residence"),
            occupation=request.form.get("occupation"),
            employer_or_business=request.form.get("employer_or_business"),
            monthly_income=request.form.get("monthly_income") or None,
            max_guaranteed_amount=request.form.get("max_guaranteed_amount") or None,
            created_by_id=current_user.id,
        )
        if not record.full_name:
            flash("Guarantor name is required.", "danger")
            return redirect(url_for("applications.guarantors"))
        stamp_tenant(record)
        db.session.add(record)
        db.session.flush()
        _guarantor_warnings(record)
        log_action("guarantor_created", "Guarantor", record.id,
                   new_value={"name": record.full_name})
        db.session.commit()
        flash("Guarantor registered.", "success")
        return redirect(url_for("applications.guarantors"))
    records = tenant_query(Guarantor).order_by(Guarantor.full_name).all()
    return render_template("applications/guarantors.html", guarantors=records)


def _guarantor_warnings(guarantor):
    warnings = []
    if (guarantor.ghana_card_expiry_date
            and guarantor.ghana_card_expiry_date < date.today()):
        warnings.append("Guarantor's Ghana Card has expired.")
    from mcag.models import User
    if guarantor.phone:
        staff = (tenant_query(User).filter(User.phone == guarantor.phone).first())
        if staff:
            warnings.append(f"Guarantor phone matches staff member {staff.full_name} "
                            "— possible conflict of interest.")
    for w in warnings:
        flash(w, "warning")
    scan_guarantor(guarantor)
    return warnings


@bp.route("/<int:app_id>/guarantors/link", methods=["POST"])
@permission_required(P_EDIT)
def link_guarantor(app_id):
    application = _get_application(app_id)
    guarantor = get_tenant_or_404(Guarantor, request.form.get("guarantor_id", type=int))
    link = GuaranteeLink(
        guarantor_id=guarantor.id,
        application_id=application.id,
        guaranteed_amount=request.form.get("guaranteed_amount") or None,
        date_signed=date.today(),
    )
    stamp_tenant(link)
    db.session.add(link)
    db.session.flush()
    if guarantor.max_guaranteed_amount:
        total = sum(D(g.guaranteed_amount or 0)
                    for g in guarantor.guarantees if g.status == "active")
        if total > D(guarantor.max_guaranteed_amount):
            flash("Warning: this guarantor's total guarantees exceed their "
                  "maximum guaranteed amount.", "warning")
    _guarantor_warnings(guarantor)
    log_action("guarantor_linked", "GuaranteeLink", link.id,
               new_value={"application": application.application_number,
                          "guarantor": guarantor.full_name})
    db.session.commit()
    flash("Guarantor linked to application.", "success")
    return redirect(url_for("applications.detail", app_id=application.id))


# ---------------------------------------------------------------------------
# Collateral
# ---------------------------------------------------------------------------
@bp.route("/<int:app_id>/collateral", methods=["POST"])
@permission_required(P_EDIT)
def add_collateral(app_id):
    application = _get_application(app_id)
    record = Collateral(
        application_id=application.id,
        customer_id=application.customer_id,
        collateral_type=request.form.get("collateral_type") or "other",
        description=request.form.get("description") or "",
        owner_name=request.form.get("owner_name"),
        owner_relationship=request.form.get("owner_relationship"),
        location=request.form.get("location"),
        estimated_market_value=request.form.get("estimated_market_value") or None,
        forced_sale_value=request.form.get("forced_sale_value") or None,
        valuation_date=(date.fromisoformat(request.form["valuation_date"])
                        if request.form.get("valuation_date") else None),
        valuer=request.form.get("valuer"),
        proof_of_ownership=request.form.get("proof_of_ownership"),
        existing_encumbrances=request.form.get("existing_encumbrances"),
        insurance_details=request.form.get("insurance_details"),
        registration_details=request.form.get("registration_details"),
        collateral_registry_reference=request.form.get("collateral_registry_reference"),
    )
    if not record.description:
        flash("Collateral description is required.", "danger")
        return redirect(url_for("applications.detail", app_id=application.id))
    stamp_tenant(record)
    db.session.add(record)
    db.session.flush()
    # Shared collateral alert
    same = (tenant_query(Collateral)
            .filter(Collateral.description == record.description,
                    Collateral.customer_id != record.customer_id).first())
    if same:
        from mcag.services.alerts import _raise_alert
        _raise_alert(current_user.institution_id, "shared_collateral",
                     "The same collateral description is linked to another customer.",
                     application.customer, "Collateral", same.id, "high")
        flash("Alert: this collateral appears to be linked to another customer.",
              "warning")
    log_action("collateral_added", "Collateral", record.id,
               new_value={"type": record.collateral_type})
    db.session.commit()
    flash("Collateral recorded.", "success")
    return redirect(url_for("applications.detail", app_id=application.id))


@bp.route("/collateral")
@permission_required(P_VIEW)
def collateral_register():
    records = tenant_query(Collateral).order_by(Collateral.created_at.desc()).all()
    return render_template("applications/collateral.html", records=records,
                           collateral_types=COLLATERAL_TYPES)


@bp.route("/collateral/<int:collateral_id>/form/<form_type>.pdf")
@permission_required(P_VIEW)
def collateral_form_pdf(collateral_id, form_type):
    if form_type not in ("inspection", "checklist", "release"):
        abort(404)
    record = get_tenant_or_404(Collateral, collateral_id)
    from mcag.services.pdf import render_pdf
    pdf = render_pdf(f"pdf/collateral_{form_type}.html", record=record,
                     institution=current_user.institution)
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition":
            f"attachment; filename=collateral-{form_type}-{record.id}.pdf"})
