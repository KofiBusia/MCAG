"""CLI commands: database init, platform admin bootstrap, development seed."""
import os
from datetime import date, timedelta
from decimal import Decimal

import click
from flask.cli import with_appcontext

from mcag.extensions import db


def register_cli(app):
    app.cli.add_command(init_db)
    app.cli.add_command(create_platform_admin)
    app.cli.add_command(seed_dev)


@click.command("init-db")
@with_appcontext
def init_db():
    """Create all database tables (use 'flask db upgrade' with migrations in
    production; this is a convenience for first-time local setup)."""
    db.create_all()
    click.echo("Database tables created.")


@click.command("create-platform-admin")
@click.option("--email", envvar="PLATFORM_ADMIN_EMAIL", prompt=True,
              help="Email (or set PLATFORM_ADMIN_EMAIL).")
@click.option("--name", default="Platform Administrator", help="Full name.")
@click.option("--password", envvar="PLATFORM_ADMIN_PASSWORD", prompt=True,
              hide_input=True, confirmation_prompt=False,
              help="Password (or set PLATFORM_ADMIN_PASSWORD).")
@with_appcontext
def create_platform_admin(email, name, password):
    """Safely create the first platform super administrator.

    Reads credentials from PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD
    environment variables or prompts. Never creates a predictable default
    password. The admin must change the password at first login.
    """
    from mcag.constants import ROLE_PLATFORM_ADMIN
    from mcag.models import User
    from mcag.utils import password_strength_errors

    email = email.strip().lower()
    errors = password_strength_errors(password)
    if errors:
        for e in errors:
            click.echo(f"ERROR: {e}", err=True)
        raise SystemExit(1)
    existing = User.query.filter(db.func.lower(User.email) == email).first()
    if existing:
        click.echo("A user with that email already exists.", err=True)
        raise SystemExit(1)
    admin = User(email=email, full_name=name, role=ROLE_PLATFORM_ADMIN,
                 institution_id=None, must_change_password=True)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    click.echo(f"Platform administrator {email} created. "
               "A password change is required at first login.")


@click.command("seed-dev")
@with_appcontext
def seed_dev():
    """Seed DEVELOPMENT data: a demo institution, staff, products, customers
    and a disbursed loan. Refuses to run in production."""
    if os.environ.get("FLASK_ENV") == "production":
        click.echo("seed-dev is disabled in production.", err=True)
        raise SystemExit(1)

    from mcag.constants import (
        ROLE_ACCOUNTS_OFFICER, ROLE_INSTITUTION_ADMIN, ROLE_LOAN_OFFICER,
        ROLE_MANAGER, METHOD_FLAT, SCHED_EQUAL_INSTALMENT,
    )
    from mcag.models import (
        CollectionZone, Customer, Institution, LoanProduct, User,
    )
    from mcag.services.accounting import seed_chart_of_accounts

    if Institution.query.filter_by(legal_name="Demo Microcredit Enterprise").first():
        click.echo("Demo data already seeded.")
        return

    inst = Institution(
        legal_name="Demo Microcredit Enterprise",
        trading_name="Demo MCE",
        mcag_membership_number="MCAG-DEMO-001",
        office_address="Kasoa New Market Road, Kasoa",
        digital_address="CX-012-0797",
        phone_primary="0244000000",
        email="info@demo-mce.example",
        proprietor_name="Ama Mensah",
        manager_name="Kofi Boateng",
        status="active",
    )
    db.session.add(inst)
    db.session.flush()
    seed_chart_of_accounts(inst)

    def add_user(email, name, role, limit=None):
        user = User(institution_id=inst.id, email=email, full_name=name,
                    role=role, must_change_password=False, approval_limit=limit)
        user.set_password("DemoPass123!")
        db.session.add(user)
        return user

    admin = add_user("admin@demo-mce.example", "Demo Admin", ROLE_INSTITUTION_ADMIN)
    manager = add_user("manager@demo-mce.example", "Kofi Boateng", ROLE_MANAGER,
                       limit=Decimal("50000"))
    officer = add_user("officer@demo-mce.example", "Efua Owusu", ROLE_LOAN_OFFICER)
    accounts = add_user("accounts@demo-mce.example", "Yaw Darko", ROLE_ACCOUNTS_OFFICER)

    zone = CollectionZone(institution_id=inst.id, name="Kasoa Market",
                          zone_type="market")
    db.session.add(zone)
    db.session.flush()

    products = [
        ("Business Loan", "BUS", METHOD_FLAT, 10, 500, 20000, 1, 12, "monthly"),
        ("Personal Loan", "PER", METHOD_FLAT, 8, 200, 10000, 1, 12, "monthly"),
        ("Contract Loan", "CON", "reducing_balance", 6, 1000, 50000, 1, 12, "monthly"),
        ("Funeral Loan", "FUN", METHOD_FLAT, 8, 200, 5000, 1, 6, "monthly"),
        ("Salary Loan", "SAL", "reducing_balance", 5, 500, 30000, 1, 24, "monthly"),
    ]
    for name, code, method, rate, min_a, max_a, min_t, max_t, freq in products:
        db.session.add(LoanProduct(
            institution_id=inst.id, name=name, code=code,
            description=f"{name} for Ghanaian microcredit customers",
            min_amount=min_a, max_amount=max_a, min_tenure=min_t,
            max_tenure=max_t, repayment_frequency=freq,
            interest_method=method, schedule_type=SCHED_EQUAL_INSTALMENT,
            min_rate=Decimal(rate) - 2, max_rate=Decimal(rate) + 2,
            rate_period="monthly",
            application_fee=Decimal("20"),
            processing_fee_percent=Decimal("2"),
            penalty_basis="overdue_instalment",
            penalty_rate_percent=Decimal("0.5"),
            penalty_grace_days=1,
            penalty_max_percent=Decimal("50"),
            guarantors_required=1,
        ))

    customers_data = [
        ("Abdulai Ibrahim", "Male", "GHA-123456789-1", "0244111111",
         "Kasoa", "Central", "Provision store"),
        ("Akosua Adjei", "Female", "GHA-987654321-2", "0244222222",
         "Kasoa New Town", "Central", "Fish trading"),
        ("Kwame Asante", "Male", "GHA-456789123-3", "0244333333",
         "Ofaakor", "Central", "Mobile phone repairs"),
    ]
    for name, sex, card, phone, location, region, business in customers_data:
        seq = inst.take_sequence("next_customer_seq")
        db.session.add(Customer(
            institution_id=inst.id,
            customer_number=f"CUS-{seq:06d}",
            full_name=name, sex=sex, ghana_card_number=card,
            phone_primary=phone, residential_location=location, region=region,
            business_name=business, business_type=business,
            date_of_birth=date(1988, 6, 15),
            nationality="Ghanaian",
            employment_type="self_employed",
            estimated_daily_sales=Decimal("450"),
            estimated_daily_expenses=Decimal("300"),
            collection_zone_id=zone.id,
            created_by_id=officer.id,
        ))

    db.session.commit()
    click.echo("Development seed complete.")
    click.echo("  Institution: Demo Microcredit Enterprise (active)")
    click.echo("  Users (password DemoPass123!):")
    click.echo("    admin@demo-mce.example    (Institution Administrator)")
    click.echo("    manager@demo-mce.example  (Manager, approval limit 50,000)")
    click.echo("    officer@demo-mce.example  (Loan Officer)")
    click.echo("    accounts@demo-mce.example (Accounts Officer)")
    click.echo("Remember to also run: flask create-platform-admin")
