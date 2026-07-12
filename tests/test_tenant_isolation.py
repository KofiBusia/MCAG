"""Mandatory tenant isolation tests — one institution must never reach
another institution's records by URL, id guessing, search or export."""
import io

from mcag.extensions import db
from mcag.models import Customer, Document
from tests.conftest import PASSWORD, create_active_loan, login


class TestCustomerIsolation:
    def test_cannot_open_other_tenants_customer_by_url(
            self, client, tenant_a, tenant_b):
        login(client, "officer@alpha.example")
        other_id = tenant_b["customer"].id
        assert client.get(f"/customers/{other_id}").status_code == 404

    def test_own_customer_is_accessible(self, client, tenant_a, tenant_b):
        login(client, "officer@alpha.example")
        own_id = tenant_a["customer"].id
        assert client.get(f"/customers/{own_id}").status_code == 200

    def test_search_only_returns_own_customers(self, client, tenant_a, tenant_b):
        login(client, "officer@alpha.example")
        resp = client.get("/customers/?q=Customer")
        assert b"Customer Alpha" in resp.data
        assert b"Customer Beta" not in resp.data

    def test_cannot_edit_other_tenants_customer(self, client, tenant_a, tenant_b):
        login(client, "officer@alpha.example")
        other_id = tenant_b["customer"].id
        resp = client.post(f"/customers/{other_id}/edit",
                           data={"full_name": "Hacked"})
        assert resp.status_code == 404
        db.session.rollback()
        assert tenant_b["customer"].full_name != "Hacked"


class TestLoanIsolation:
    def test_cannot_open_other_tenants_loan(self, client, tenant_a, tenant_b):
        loan_b, _ = create_active_loan(tenant_b)
        login(client, "officer@alpha.example")
        assert client.get(f"/loans/{loan_b.id}").status_code == 404
        assert client.get(f"/loans/{loan_b.id}/ledger").status_code == 404
        assert client.get(f"/loans/{loan_b.id}/statement.pdf").status_code == 404

    def test_cannot_open_other_tenants_application(self, client, tenant_a, tenant_b):
        loan_b, _ = create_active_loan(tenant_b)
        login(client, "officer@alpha.example")
        assert client.get(
            f"/applications/{loan_b.application_id}").status_code == 404


class TestDocumentIsolation:
    def test_cannot_download_other_tenants_document(
            self, client, tenant_a, tenant_b, app):
        # Upload a document as tenant B
        login(client, "officer@beta.example")
        resp = client.post(
            f"/customers/{tenant_b['customer'].id}/documents",
            data={"document_type": "Ghana Card",
                  "file": (io.BytesIO(b"beta secret card"), "card.pdf")},
            content_type="multipart/form-data", follow_redirects=True)
        assert resp.status_code == 200
        document = Document.query.filter_by(
            institution_id=tenant_b["institution"].id).one()
        client.post("/logout")

        login(client, "officer@alpha.example")
        assert client.get(
            f"/documents/{document.id}/download").status_code == 404

    def test_owner_can_download(self, client, tenant_b, app):
        login(client, "officer@beta.example")
        client.post(
            f"/customers/{tenant_b['customer'].id}/documents",
            data={"document_type": "Ghana Card",
                  "file": (io.BytesIO(b"beta secret card"), "card.pdf")},
            content_type="multipart/form-data")
        document = Document.query.one()
        resp = client.get(f"/documents/{document.id}/download")
        assert resp.status_code == 200
        assert resp.data == b"beta secret card"


class TestExportIsolation:
    def test_inspection_export_only_own_records(self, client, tenant_a, tenant_b):
        login(client, "admin@alpha.example")
        resp = client.get("/reports/inspection/customers.csv")
        body = resp.data.decode()
        assert "Customer Alpha" in body
        assert "Customer Beta" not in body

    def test_bureau_export_only_own_loans(self, client, tenant_a, tenant_b):
        loan_a, _ = create_active_loan(tenant_a)
        loan_b, _ = create_active_loan(tenant_b)
        login(client, "admin@alpha.example")
        resp = client.get("/compliance/credit-bureau/export.csv")
        body = resp.data.decode()
        assert loan_a.loan_number in body
        assert "Customer Beta" not in body


class TestAuditIsolation:
    def test_audit_logs_scoped_to_institution(self, client, tenant_a, tenant_b):
        create_active_loan(tenant_b)
        login(client, "admin@alpha.example")
        resp = client.get("/compliance/audit-logs")
        assert resp.status_code == 200
        assert b"beta.example" not in resp.data
