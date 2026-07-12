"""Configurable loan products."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import (
    FREQUENCIES, INTEREST_METHODS, P_MANAGE_PRODUCTS, P_VIEW, SCHEDULE_TYPES,
)
from mcag.extensions import db
from mcag.models import LoanProduct
from mcag.services.audit import log_action
from mcag.services.tenancy import get_tenant_or_404, stamp_tenant, tenant_query
from mcag.utils import D

bp = Blueprint("products", __name__)

FIELDS = [
    "name", "code", "description", "min_amount", "max_amount", "min_tenure",
    "max_tenure", "repayment_frequency", "interest_method", "schedule_type",
    "min_rate", "max_rate", "rate_period", "application_fee",
    "processing_fee_percent", "processing_fee_fixed", "other_fees",
    "penalty_basis", "penalty_rate_percent", "penalty_fixed_amount",
    "penalty_grace_days", "penalty_max_percent",
    "early_settlement_charge_percent", "early_settlement_terms",
    "grace_periods", "guarantors_required", "required_documents",
    "approval_authority",
]


def _penalty_warnings(product) -> list:
    """Compliance warnings on penalty configuration (never hard-coded)."""
    warnings = []
    inst = current_user.institution
    max_daily = D(inst.setting("penalty_max_daily_percent", "1"))
    if D(product.penalty_rate_percent) > max_daily:
        warnings.append(
            f"Penalty rate {product.penalty_rate_percent}% per day exceeds the "
            f"institution limit of {max_daily}% and appears excessive. The "
            "MCAG sample's 10%/day charge is not a compliant default.")
    if product.penalty_basis == "overdue_principal":
        warnings.append(
            "Penalty applies to the whole outstanding principal instead of the "
            "overdue instalment — review compliance before use.")
    return warnings


def _apply(product):
    errors = []
    for field in FIELDS:
        if field in request.form:
            value = request.form.get(field)
            setattr(product, field, value if value != "" else None)
    for field in ("application_fee", "processing_fee_percent", "processing_fee_fixed",
                  "other_fees", "penalty_rate_percent", "penalty_fixed_amount"):
        if getattr(product, field) is None:
            setattr(product, field, 0)
    if product.penalty_grace_days is None:
        product.penalty_grace_days = 0
    if product.grace_periods is None:
        product.grace_periods = 0
    if product.guarantors_required is None:
        product.guarantors_required = 0
    if product.early_settlement_charge_percent is None:
        product.early_settlement_charge_percent = 0
    product.fees_deducted_upfront = bool(request.form.get("fees_deducted_upfront"))
    product.collateral_required = bool(request.form.get("collateral_required"))
    product.active = bool(request.form.get("active", "1"))
    if not product.name or not product.code:
        errors.append("Product name and code are required.")
    try:
        if D(product.min_amount) <= 0 or D(product.max_amount) < D(product.min_amount):
            errors.append("Check minimum and maximum amounts.")
        if int(product.min_tenure) < 1 or int(product.max_tenure) < int(product.min_tenure):
            errors.append("Check minimum and maximum tenure.")
        if D(product.min_rate) < 0 or D(product.max_rate) < D(product.min_rate):
            errors.append("Check minimum and maximum interest rate.")
    except (TypeError, ValueError):
        errors.append("Amounts, tenure and rates must be numeric.")
    return errors


@bp.route("/")
@permission_required(P_VIEW)
def index():
    records = tenant_query(LoanProduct).order_by(LoanProduct.name).all()
    return render_template("products/index.html", products=records)


@bp.route("/new", methods=["GET", "POST"])
@permission_required(P_MANAGE_PRODUCTS)
def new():
    if request.method == "POST":
        product = LoanProduct()
        stamp_tenant(product)
        errors = _apply(product)
        if not errors and tenant_query(LoanProduct).filter_by(code=product.code).first():
            errors.append("A product with that code already exists.")
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("products/form.html", product=None,
                                   frequencies=FREQUENCIES,
                                   methods=INTEREST_METHODS,
                                   schedule_types=SCHEDULE_TYPES)
        db.session.add(product)
        for w in _penalty_warnings(product):
            flash(w, "warning")
        log_action("product_created", "LoanProduct", None,
                   new_value={"code": product.code, "name": product.name})
        db.session.commit()
        flash("Loan product created.", "success")
        return redirect(url_for("products.index"))
    return render_template("products/form.html", product=None,
                           frequencies=FREQUENCIES, methods=INTEREST_METHODS,
                           schedule_types=SCHEDULE_TYPES)


@bp.route("/<int:product_id>", methods=["GET", "POST"])
@permission_required(P_VIEW)
def detail(product_id):
    product = get_tenant_or_404(LoanProduct, product_id)
    if request.method == "POST":
        if not current_user.can(P_MANAGE_PRODUCTS):
            flash("You do not have permission to edit products.", "danger")
            return redirect(url_for("products.detail", product_id=product.id))
        old = {"rate": str(product.min_rate) + "-" + str(product.max_rate)}
        errors = _apply(product)
        if errors:
            for e in errors:
                flash(e, "danger")
        else:
            for w in _penalty_warnings(product):
                flash(w, "warning")
            log_action("product_updated", "LoanProduct", product.id, old_value=old)
            db.session.commit()
            flash("Product updated.", "success")
        return redirect(url_for("products.detail", product_id=product.id))
    return render_template("products/form.html", product=product,
                           frequencies=FREQUENCIES, methods=INTEREST_METHODS,
                           schedule_types=SCHEDULE_TYPES)
