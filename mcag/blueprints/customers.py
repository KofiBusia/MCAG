"""Customer registration (MCAG Loan Application Form personal data),
documents, duplicate alerts, Word export of the application form."""
from datetime import date

from flask import (
    Blueprint, Response, flash, redirect, render_template, request, url_for,
)
from flask_login import current_user

from mcag.blueprints.helpers import page_args, permission_required
from mcag.constants import GHANA_REGIONS, P_CREATE, P_EDIT, P_VIEW, DOCUMENT_TYPES
from mcag.extensions import db
from mcag.models import CollectionZone, Customer, Document
from mcag.services.alerts import scan_customer
from mcag.services.audit import log_action
from mcag.services.documents import DocumentError, save_upload
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import valid_gh_phone, valid_ghana_card

bp = Blueprint("customers", __name__)

TEXT_FIELDS = [
    "full_name", "alias", "sex", "place_of_birth", "nationality", "home_town",
    "region", "ghana_card_number", "marital_status", "phone_primary",
    "phone_secondary", "house_number", "residential_digital_address",
    "residential_location", "residential_landmark", "accommodation_status",
    "landlord_name", "employment_type", "business_name", "business_type",
    "business_location", "business_landmark", "premises_type", "premises_status",
    "employer_name", "employer_location", "employer_business_type", "position",
    "bank_name", "bank_branch", "bank_account_name", "bank_account_number",
    "momo_provider", "momo_number", "momo_name", "source_of_repayment",
    "existing_loans_details", "spouse_name", "spouse_phone", "spouse_occupation",
    "next_of_kin_name", "next_of_kin_relationship", "next_of_kin_phone",
    "next_of_kin_address", "relatives_info", "references_info",
]
SENSITIVE_FIELDS = ["ethnicity", "religion", "place_of_worship",
                    "worship_location", "worship_leader"]
NUMBER_FIELDS = [
    "dependants", "cycle_number", "years_at_residence", "years_in_business",
    "years_at_business_location", "estimated_daily_sales",
    "estimated_daily_expenses", "estimated_working_capital",
    "number_of_employees", "other_income", "years_employed",
    "net_monthly_salary", "household_income", "household_expenses",
    "existing_monthly_repayments",
]
DATE_FIELDS = ["date_of_birth", "ghana_card_issue_date",
               "ghana_card_expiry_date", "rent_expiry_date"]


def _apply_form(customer: Customer):
    errors = []
    for field in TEXT_FIELDS:
        if field in request.form:
            setattr(customer, field, (request.form.get(field) or "").strip() or None)
    for field in NUMBER_FIELDS:
        value = (request.form.get(field) or "").strip()
        setattr(customer, field, value or None)
    for field in DATE_FIELDS:
        value = request.form.get(field)
        setattr(customer, field, date.fromisoformat(value) if value else None)
    customer.collection_zone_id = request.form.get("collection_zone_id", type=int) or None

    # Sensitive optional fields: only when institution explicitly enabled them.
    if current_user.institution.setting("sensitive_fields_enabled"):
        for field in SENSITIVE_FIELDS:
            if field in request.form:
                setattr(customer, field, (request.form.get(field) or "").strip() or None)
    else:
        for field in SENSITIVE_FIELDS:
            setattr(customer, field, None)

    if not customer.full_name:
        errors.append("Full name is required.")
    if customer.ghana_card_number and not valid_ghana_card(customer.ghana_card_number):
        errors.append("Ghana Card number must be in the format GHA-XXXXXXXXX-X.")
    if customer.phone_primary and not valid_gh_phone(customer.phone_primary):
        errors.append("Primary phone must be a valid Ghanaian number "
                      "(0XXXXXXXXX or +233XXXXXXXXX).")
    return errors


@bp.route("/")
@permission_required(P_VIEW)
def index():
    page, per_page = page_args()
    q = (request.args.get("q") or "").strip()
    query = tenant_query(Customer)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            Customer.full_name.ilike(like),
            Customer.customer_number.ilike(like),
            Customer.phone_primary.ilike(like)))
    pagination = query.order_by(Customer.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False)
    return render_template("customers/index.html", pagination=pagination, q=q)


@bp.route("/new", methods=["GET", "POST"])
@permission_required(P_CREATE)
def new():
    zones = tenant_query(CollectionZone).filter_by(active=True).all()
    if request.method == "POST":
        customer = Customer()
        stamp_tenant(customer)
        errors = _apply_form(customer)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("customers/form.html", customer=None,
                                   zones=zones, regions=GHANA_REGIONS)
        inst = current_user.institution
        seq = inst.take_sequence("next_customer_seq")
        customer.customer_number = f"CUS-{seq:06d}"
        customer.created_by_id = current_user.id
        db.session.add(customer)
        db.session.flush()
        alerts = scan_customer(customer)
        log_action("customer_created", "Customer", customer.id,
                   new_value={"number": customer.customer_number,
                              "name": customer.full_name})
        db.session.commit()
        if alerts:
            flash(f"{len(alerts)} duplicate/fraud alert(s) raised for review — "
                  "the customer record still requires human review before "
                  "any decision.", "warning")
        flash(f"Customer {customer.customer_number} registered.", "success")
        return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", customer=None, zones=zones,
                           regions=GHANA_REGIONS)


@bp.route("/<int:customer_id>")
@permission_required(P_VIEW)
def detail(customer_id):
    customer = get_tenant_or_404(Customer, customer_id)
    documents = tenant_query(Document).filter_by(customer_id=customer.id).all()
    return render_template("customers/detail.html", customer=customer,
                           documents=documents, document_types=DOCUMENT_TYPES)


@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@permission_required(P_EDIT)
def edit(customer_id):
    customer = get_tenant_or_404(Customer, customer_id)
    zones = tenant_query(CollectionZone).filter_by(active=True).all()
    if request.method == "POST":
        old = {f: str(getattr(customer, f)) for f in TEXT_FIELDS[:10]}
        errors = _apply_form(customer)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("customers/form.html", customer=customer,
                                   zones=zones, regions=GHANA_REGIONS)
        scan_customer(customer)
        log_action("customer_updated", "Customer", customer.id, old_value=old)
        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("customers.detail", customer_id=customer.id))
    return render_template("customers/form.html", customer=customer, zones=zones,
                           regions=GHANA_REGIONS)


@bp.route("/<int:customer_id>/documents", methods=["POST"])
@permission_required(P_EDIT)
def upload_document(customer_id):
    customer = get_tenant_or_404(Customer, customer_id)
    file = request.files.get("file")
    doc_type = request.form.get("document_type") or "Other"
    expiry = request.form.get("expiry_date")
    try:
        save_upload(
            file, current_user.institution_id, doc_type, current_user,
            customer_id=customer.id,
            expiry_date=date.fromisoformat(expiry) if expiry else None)
        db.session.commit()
        flash("Document uploaded.", "success")
    except DocumentError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("customers.detail", customer_id=customer.id))


@bp.route("/<int:customer_id>/application-form.docx")
@permission_required(P_VIEW)
def application_form_docx(customer_id):
    """Completed MCAG Loan Application Form as a Word document."""
    from mcag.services.word_export import loan_application_docx
    customer = get_tenant_or_404(Customer, customer_id)
    data = loan_application_docx(current_user.institution, customer=customer)
    log_action("document_download", "Customer", customer.id,
               new_value={"file": "loan application form (filled)"})
    db.session.commit()
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f"attachment; filename=LOAN Application form - {customer.customer_number}.docx"})


@bp.route("/blank-application-form.docx")
@permission_required(P_VIEW)
def blank_application_form_docx():
    """Blank MCAG Loan Application Form for printing and manual completion."""
    from mcag.services.word_export import loan_application_docx
    data = loan_application_docx(current_user.institution)
    return Response(
        data,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 "attachment; filename=LOAN Application form.docx"})
