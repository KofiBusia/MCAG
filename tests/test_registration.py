"""Institution self-registration (signup) and approval flow."""
from mcag.extensions import db
from mcag.models import Account, Institution, User
from tests.conftest import PASSWORD, login

SIGNUP = {
    "legal_name": "Gamma Micro-Credit Enterprise",
    "trading_name": "Gamma MCE",
    "mcag_membership_number": "MCAG-GAMMA",
    "office_address": "Ofaakor Main Road",
    "phone_primary": "0244555555",
    "admin_name": "Adwoa Gamma",
    "admin_email": "admin@gamma.example",
    "password": "GammaPass123!x",
    "confirm_password": "GammaPass123!x",
    "declaration": "1",
}


class TestSignup:
    def test_register_page_linked_from_login(self, client, app):
        resp = client.get("/login")
        assert b"Register your enterprise" in resp.data
        assert client.get("/register").status_code == 200

    def test_signup_creates_pending_institution(self, client, app):
        resp = client.post("/register", data=SIGNUP, follow_redirects=True)
        assert resp.status_code == 200
        assert b"pending approval" in resp.data
        inst = Institution.query.filter_by(
            legal_name="Gamma Micro-Credit Enterprise").one()
        assert inst.status == "pending"
        admin = User.query.filter_by(email="admin@gamma.example").one()
        assert admin.institution_id == inst.id
        assert admin.role == "institution_admin"
        # chart of accounts seeded on signup
        assert Account.query.filter_by(institution_id=inst.id).count() > 0

    def test_pending_institution_cannot_sign_in(self, client, app):
        client.post("/register", data=SIGNUP)
        resp = client.post("/login", data={
            "email": "admin@gamma.example", "password": SIGNUP["password"]})
        assert resp.status_code == 403
        assert b"pending approval" in resp.data

    def test_approved_institution_can_sign_in(self, client, app, platform_admin):
        client.post("/register", data=SIGNUP)
        inst = Institution.query.filter_by(
            legal_name="Gamma Micro-Credit Enterprise").one()
        # platform admin approves
        login(client, "platform@mcag.example")
        client.post(f"/platform/institutions/{inst.id}/status",
                    data={"action": "approve"})
        assert inst.status == "active"
        client.post("/logout")
        resp = login(client, "admin@gamma.example", SIGNUP["password"])
        assert b"Dashboard" in resp.data

    def test_weak_password_rejected(self, client, app):
        data = dict(SIGNUP, password="weak", confirm_password="weak")
        resp = client.post("/register", data=data)
        assert resp.status_code == 400
        assert Institution.query.count() == 0

    def test_duplicate_email_rejected(self, client, app, tenant_a):
        data = dict(SIGNUP, admin_email="admin@alpha.example")
        resp = client.post("/register", data=data)
        assert resp.status_code == 400

    def test_declaration_required(self, client, app):
        data = dict(SIGNUP)
        data.pop("declaration")
        resp = client.post("/register", data=data)
        assert resp.status_code == 400
        assert Institution.query.count() == 0
