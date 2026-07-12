"""Authentication, lockout and permission tests."""
from mcag.extensions import db
from mcag.models import LoginEvent, User
from tests.conftest import PASSWORD, login


class TestLogin:
    def test_successful_login(self, client, tenant_a):
        resp = login(client, "officer@alpha.example")
        assert resp.status_code == 200
        assert b"Dashboard" in resp.data

    def test_wrong_password_fails(self, client, tenant_a):
        resp = client.post("/login", data={
            "email": "officer@alpha.example", "password": "wrong"})
        assert resp.status_code == 401

    def test_unknown_email_fails_and_logged(self, client, tenant_a):
        resp = client.post("/login", data={
            "email": "nobody@alpha.example", "password": "x"})
        assert resp.status_code == 401
        assert LoginEvent.query.filter_by(event="failed").count() == 1

    def test_lockout_after_max_attempts(self, client, tenant_a):
        for _ in range(3):  # TestingConfig.MAX_LOGIN_ATTEMPTS = 3
            client.post("/login", data={
                "email": "officer@alpha.example", "password": "wrong"})
        user = User.query.filter_by(email="officer@alpha.example").one()
        assert user.is_locked
        resp = client.post("/login", data={
            "email": "officer@alpha.example", "password": PASSWORD})
        assert resp.status_code == 423

    def test_deactivated_user_cannot_login(self, client, tenant_a):
        user = User.query.filter_by(email="officer@alpha.example").one()
        user.is_active_user = False
        db.session.commit()
        resp = client.post("/login", data={
            "email": "officer@alpha.example", "password": PASSWORD})
        assert resp.status_code == 403

    def test_suspended_institution_blocks_login(self, client, tenant_a):
        tenant_a["institution"].status = "suspended"
        db.session.commit()
        resp = client.post("/login", data={
            "email": "officer@alpha.example", "password": PASSWORD})
        assert resp.status_code == 403

    def test_anonymous_redirected_to_login(self, client, tenant_a):
        resp = client.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


class TestPermissions:
    def test_officer_cannot_manage_users(self, client, tenant_a):
        login(client, "officer@alpha.example")
        assert client.get("/institution/users").status_code == 403

    def test_admin_can_manage_users(self, client, tenant_a):
        login(client, "admin@alpha.example")
        assert client.get("/institution/users").status_code == 200

    def test_platform_admin_cannot_open_institution_pages(
            self, client, tenant_a, platform_admin):
        login(client, "platform@mcag.example")
        assert client.get("/customers/").status_code == 403

    def test_institution_user_cannot_open_platform_pages(self, client, tenant_a):
        login(client, "admin@alpha.example")
        assert client.get("/platform/").status_code == 403


class TestPasswordChange:
    def test_change_password_flow(self, client, tenant_a):
        login(client, "officer@alpha.example")
        resp = client.post("/change-password", data={
            "current_password": PASSWORD,
            "new_password": "NewSecret123!x",
            "confirm_password": "NewSecret123!x",
        }, follow_redirects=True)
        assert resp.status_code == 200
        user = User.query.filter_by(email="officer@alpha.example").one()
        assert user.check_password("NewSecret123!x")

    def test_weak_password_rejected(self, client, tenant_a):
        login(client, "officer@alpha.example")
        client.post("/change-password", data={
            "current_password": PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
        })
        user = User.query.filter_by(email="officer@alpha.example").one()
        assert user.check_password(PASSWORD)  # unchanged
