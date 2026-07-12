import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("FLASK_ENV", "testing")

from mcag import create_app
from mcag.config import TestingConfig
from mcag.constants import (
    METHOD_FLAT, ROLE_ACCOUNTS_OFFICER, ROLE_INSTITUTION_ADMIN,
    ROLE_LOAN_OFFICER, ROLE_MANAGER, ROLE_PLATFORM_ADMIN,
    SCHED_EQUAL_INSTALMENT,
)
from mcag.extensions import db
from mcag.models import (
    CollectionZone, Customer, Institution, LoanProduct, User,
)
from mcag.services.accounting import seed_chart_of_accounts

PASSWORD = "TestPass123!x"


@pytest.fixture()
def app(tmp_path):
    app = create_app(TestingConfig)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_institution(name="Alpha Micro-Credit Enterprise", suffix="alpha"):
    inst = Institution(
        legal_name=name,
        trading_name=name.split()[0],
        mcag_membership_number=f"MCAG-{suffix.upper()}",
        office_address="Kasoa New Market Road",
        phone_primary="0244000001",
        status="active",
    )
    db.session.add(inst)
    db.session.flush()
    seed_chart_of_accounts(inst)

    users = {}
    for role, key in [
        (ROLE_INSTITUTION_ADMIN, "admin"),
        (ROLE_MANAGER, "manager"),
        (ROLE_LOAN_OFFICER, "officer"),
        (ROLE_ACCOUNTS_OFFICER, "accounts"),
    ]:
        user = User(
            institution_id=inst.id,
            email=f"{key}@{suffix}.example",
            full_name=f"{key.title()} {suffix.title()}",
            role=role,
            must_change_password=False,
            approval_limit=Decimal("100000") if role == ROLE_MANAGER else None,
        )
        user.set_password(PASSWORD)
        db.session.add(user)
        users[key] = user

    zone = CollectionZone(institution_id=inst.id, name=f"{suffix} zone")
    db.session.add(zone)

    product = LoanProduct(
        institution_id=inst.id,
        name="Business Loan", code="BUS",
        min_amount=Decimal("100"), max_amount=Decimal("50000"),
        min_tenure=1, max_tenure=24,
        repayment_frequency="monthly",
        interest_method=METHOD_FLAT,
        schedule_type=SCHED_EQUAL_INSTALMENT,
        min_rate=Decimal("4"), max_rate=Decimal("12"),
        rate_period="monthly",
        application_fee=Decimal("20"),
        processing_fee_percent=Decimal("2"),
        penalty_basis="overdue_instalment",
        penalty_rate_percent=Decimal("0.5"),
        penalty_grace_days=0,
        guarantors_required=0,
        collateral_required=False,
    )
    db.session.add(product)

    seq = inst.take_sequence("next_customer_seq")
    customer = Customer(
        institution_id=inst.id,
        customer_number=f"CUS-{seq:06d}",
        full_name=f"Customer {suffix.title()}",
        sex="Female",
        ghana_card_number=f"GHA-{abs(hash(suffix)) % 10**9:09d}-1",
        phone_primary="0244111111",
        region="Central",
        date_of_birth=date(1990, 1, 1),
        created_by_id=users["officer"].id,
        collection_zone_id=zone.id,
    )
    db.session.add(customer)
    db.session.flush()
    return {"institution": inst, "users": users, "product": product,
            "customer": customer, "zone": zone}


@pytest.fixture()
def tenant_a(app):
    data = make_institution("Alpha Micro-Credit Enterprise", "alpha")
    db.session.commit()
    return data


@pytest.fixture()
def tenant_b(app):
    data = make_institution("Beta Micro-Credit Enterprise", "beta")
    db.session.commit()
    return data


@pytest.fixture()
def platform_admin(app):
    user = User(email="platform@mcag.example", full_name="Platform Admin",
                role=ROLE_PLATFORM_ADMIN, must_change_password=False)
    user.set_password(PASSWORD)
    db.session.add(user)
    db.session.commit()
    return user


def login(client, email, password=PASSWORD):
    return client.post("/login", data={"email": email, "password": password},
                       follow_redirects=True)


def create_active_loan(tenant, principal="2500", rate="6", tenure=4):
    """Create an approved application and a disbursed active loan via the
    real service layer."""
    from mcag.models import LoanApplication, Disbursement
    from mcag.services.loan_engine import build_schedule
    from mcag.services.loan_service import (
        complete_disbursement, create_loan_from_application,
    )

    inst = tenant["institution"]
    product = tenant["product"]
    seq = inst.take_sequence("next_application_seq")
    application = LoanApplication(
        institution_id=inst.id,
        application_number=f"APP-{seq:06d}",
        application_date=date.today(),
        customer_id=tenant["customer"].id,
        product_id=product.id,
        loan_purpose="Working capital",
        purpose_sector="Commerce / Trading",
        amount_requested=Decimal(principal),
        proposed_tenure=tenure,
        repayment_frequency=product.repayment_frequency,
        declaration_accepted=True,
        created_by_id=tenant["users"]["officer"].id,
        status="Approved",
        approved_amount=Decimal(principal),
        approved_tenure=tenure,
        approved_rate=Decimal(rate),
        approved_by_id=tenant["users"]["manager"].id,
    )
    db.session.add(application)
    db.session.flush()

    calc = build_schedule(
        principal=Decimal(principal), rate_percent=Decimal(rate),
        rate_period=product.rate_period,
        interest_method=product.interest_method,
        schedule_type=product.schedule_type,
        frequency=product.repayment_frequency,
        tenure=tenure, disbursement_date=date.today(),
        application_fee=product.application_fee,
        processing_fee_percent=product.processing_fee_percent,
        fees_deducted_upfront=True,
    )
    loan = create_loan_from_application(
        application, calc, inst, tenant["users"]["accounts"])
    db.session.flush()
    disbursement = Disbursement(
        institution_id=inst.id,
        loan_id=loan.id,
        gross_principal=calc["principal"],
        fees_deducted=calc["total_fees"],
        net_amount=calc["net_amount_received"],
        disbursement_date=date.today(),
        method="cash",
        initiated_by_id=tenant["users"]["accounts"].id,
        authorised_by_id=tenant["users"]["manager"].id,
    )
    db.session.add(disbursement)
    db.session.flush()
    complete_disbursement(loan, disbursement, inst, tenant["users"]["manager"])
    db.session.commit()
    return loan, calc
