"""Loan approval workflow, maker-checker controls, offers and documents
through the HTTP layer."""
import json
from datetime import date
from decimal import Decimal

from mcag.extensions import db
from mcag.models import (
    AuditLog, CreditBureauEnquiry, Disbursement, GuaranteeLink, Guarantor,
    LoanApplication, OfferLetter,
)
from tests.conftest import PASSWORD, login

D = Decimal


def create_application(client, tenant):
    resp = client.post("/applications/new", data={
        "customer_id": tenant["customer"].id,
        "product_id": tenant["product"].id,
        "loan_purpose": "Stock purchase",
        "purpose_sector": "Commerce / Trading",
        "amount_requested": "2500",
        "proposed_tenure": "4",
        "proposed_payment_method": "cash",
        "declaration_accepted": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200
    return LoanApplication.query.order_by(LoanApplication.id.desc()).first()


class TestWorkflow:
    def test_full_application_to_offer_flow(self, client, tenant_a):
        # Officer creates
        login(client, "officer@alpha.example")
        application = create_application(client, tenant_a)
        assert application.status == "Submitted"

        # Officer records field verification
        client.post(f"/applications/{application.id}/field-verification", data={
            "visit_date": date.today().isoformat(),
            "business_visited": "1", "outcome": "satisfactory",
        })
        assert application.status == "Field Verification"
        client.post("/logout")

        # Manager records assessment with recommendation
        login(client, "manager@alpha.example")
        client.post(f"/applications/{application.id}/assessment", data={
            "monthly_sales": "5000", "cost_of_sales": "2000",
            "operating_expenses": "800", "household_expenses": "700",
            "existing_loan_repayments": "0", "proposed_instalment": "775",
            "amount_recommended": "2500", "tenure_recommended": "4",
            "rate_recommended": "6", "risk_rating": "Moderate",
            "recommendation": "approve",
        })
        assert application.status == "Recommended"
        assessment = application.assessments[0]
        assert assessment.net_disposable_income == D("1500.00")
        assert assessment.repayment_surplus == D("725.00")

        # Manager approves (different from creator)
        resp = client.post(f"/applications/{application.id}/approve", data={
            "decision": "approve", "approved_amount": "2500",
            "approved_tenure": "4", "approved_rate": "6",
        }, follow_redirects=True)
        assert application.status == "Approved"
        assert application.approved_by_id == tenant_a["users"]["manager"].id

        # Generate offer — figures locked from the engine
        client.post(f"/applications/{application.id}/offer",
                    data={"validity_days": "30"}, follow_redirects=True)
        offer = OfferLetter.query.filter_by(application_id=application.id).one()
        calc = json.loads(offer.calculation_json)
        assert calc["total_repayment"] == "3100.00"
        assert application.status == "Offer Issued"

        # Accept, generate agreement
        client.post(f"/applications/{application.id}/offer/{offer.id}/decision",
                    data={"decision": "accept"})
        assert offer.status == "accepted"
        client.post(f"/applications/{application.id}/agreement", data={
            "language_explained": "Twi", "witness_name": "Kwesi Mensah",
        })
        agreement = application.agreements[0]
        assert agreement.calculation_json == offer.calculation_json

        # PDF and Word downloads work
        assert client.get(
            f"/applications/{application.id}/offer/{offer.id}.pdf"
        ).status_code == 200
        assert client.get(
            f"/applications/{application.id}/offer/{offer.id}.docx"
        ).status_code == 200
        assert client.get(
            f"/applications/{application.id}/agreement/{agreement.id}.pdf"
        ).status_code == 200

    def test_creator_cannot_approve_own_application(self, client, tenant_a):
        # Manager creates AND tries to approve
        login(client, "manager@alpha.example")
        application = create_application(client, tenant_a)
        application.status = "Recommended"
        db.session.commit()
        resp = client.get(f"/applications/{application.id}/approve",
                          follow_redirects=True)
        assert b"Maker-checker" in resp.data
        assert application.status == "Recommended"

    def test_rate_outside_product_range_rejected(self, client, tenant_a):
        login(client, "officer@alpha.example")
        application = create_application(client, tenant_a)
        application.status = "Recommended"
        db.session.commit()
        client.post("/logout")
        login(client, "manager@alpha.example")
        client.post(f"/applications/{application.id}/approve", data={
            "decision": "approve", "approved_amount": "2500",
            "approved_tenure": "4", "approved_rate": "99",
        }, follow_redirects=True)
        assert application.status == "Recommended"  # not approved

    def test_approval_limit_enforced(self, client, tenant_a):
        manager = tenant_a["users"]["manager"]
        manager.approval_limit = D("1000")
        db.session.commit()
        login(client, "officer@alpha.example")
        application = create_application(client, tenant_a)
        application.status = "Recommended"
        db.session.commit()
        client.post("/logout")
        login(client, "manager@alpha.example")
        client.post(f"/applications/{application.id}/approve", data={
            "decision": "approve", "approved_amount": "2500",
            "approved_tenure": "4", "approved_rate": "6",
        }, follow_redirects=True)
        assert application.status == "Recommended"


class TestDisbursementControls:
    def _approved_application(self, client, tenant):
        login(client, "officer@alpha.example")
        application = create_application(client, tenant)
        client.post("/logout")
        application.status = "Recommended"
        db.session.commit()
        login(client, "manager@alpha.example")
        client.post(f"/applications/{application.id}/approve", data={
            "decision": "approve", "approved_amount": "2500",
            "approved_tenure": "4", "approved_rate": "6"})
        client.post(f"/applications/{application.id}/offer",
                    data={"validity_days": "30"})
        offer = OfferLetter.query.filter_by(application_id=application.id).one()
        client.post(f"/applications/{application.id}/offer/{offer.id}/decision",
                    data={"decision": "accept"})
        client.post(f"/applications/{application.id}/agreement", data={})
        agreement = application.agreements[0]
        client.post(
            f"/applications/{application.id}/agreement/{agreement.id}/execute",
            data={})
        client.post("/logout")
        return application

    def test_disbursement_blocked_without_checks(self, client, tenant_a):
        application = self._approved_application(client, tenant_a)
        # No verified Ghana Card, no bureau record → checklist fails
        login(client, "accounts@alpha.example")
        resp = client.post(
            f"/loans/disbursements/initiate/{application.id}",
            data={"method": "cash"}, follow_redirects=True)
        assert b"Pre-disbursement checks failed" in resp.data
        assert Disbursement.query.count() == 0

    def test_disbursement_maker_checker(self, client, tenant_a):
        import io
        application = self._approved_application(client, tenant_a)
        # Satisfy checklist: verified ID + bureau record
        login(client, "officer@alpha.example")
        client.post(f"/customers/{tenant_a['customer'].id}/documents", data={
            "document_type": "Ghana Card",
            "file": (io.BytesIO(b"card"), "card.pdf")},
            content_type="multipart/form-data")
        from mcag.models import Document
        document = Document.query.filter_by(document_type="Ghana Card").first()
        client.post(f"/documents/{document.id}/verify",
                    data={"decision": "verify"})
        db.session.add(CreditBureauEnquiry(
            institution_id=tenant_a["institution"].id,
            customer_id=tenant_a["customer"].id,
            application_id=application.id,
            consent_given=True, bureau_name="XDS Data",
            enquiry_date=date.today(),
            officer_id=tenant_a["users"]["officer"].id))
        db.session.commit()
        client.post("/logout")

        # Accounts officer initiates
        login(client, "accounts@alpha.example")
        resp = client.post(
            f"/loans/disbursements/initiate/{application.id}",
            data={"method": "cash"}, follow_redirects=True)
        disbursement = Disbursement.query.one()
        assert disbursement.status == "pending"

        # Same officer cannot authorise (has approve? accounts officer lacks
        # P_APPROVE so 403; use manager to authorise instead)
        resp = client.post(
            f"/loans/disbursements/{disbursement.id}/authorise")
        assert resp.status_code == 403
        client.post("/logout")

        login(client, "manager@alpha.example")
        client.post(f"/loans/disbursements/{disbursement.id}/authorise",
                    follow_redirects=True)
        assert disbursement.status == "completed"
        assert disbursement.loan.status == "Active"
        assert application.status == "Disbursed"


class TestAuditTrail:
    def test_actions_are_audited(self, client, tenant_a):
        login(client, "officer@alpha.example")
        create_application(client, tenant_a)
        actions = {a.action for a in AuditLog.query.all()}
        assert "login" in actions
        assert "application_created" in actions
