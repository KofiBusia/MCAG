"""Secure document access: authenticated, tenant-checked downloads only.
Storage keys are random — there are no predictable URLs."""
import os

from flask import Blueprint, abort, flash, redirect, request, send_file, url_for
from flask_login import current_user

from mcag.blueprints.helpers import permission_required
from mcag.constants import P_EDIT, P_VIEW
from mcag.extensions import db
from mcag.models import Document
from mcag.models.base import utcnow
from mcag.services.audit import log_action
from mcag.services.documents import storage_path
from mcag.services.tenancy import get_tenant_or_404

bp = Blueprint("documents", __name__)


@bp.route("/<int:document_id>/download")
@permission_required(P_VIEW)
def download(document_id):
    document = get_tenant_or_404(Document, document_id)
    path = storage_path(document)
    if not os.path.exists(path):
        abort(404)
    log_action("document_download", "Document", document.id,
               new_value={"type": document.document_type,
                          "filename": document.original_filename})
    db.session.commit()
    return send_file(path, as_attachment=True,
                     download_name=document.original_filename,
                     mimetype=document.content_type or "application/octet-stream")


@bp.route("/<int:document_id>/verify", methods=["POST"])
@permission_required(P_EDIT)
def verify(document_id):
    document = get_tenant_or_404(Document, document_id)
    decision = request.form.get("decision")
    if decision == "verify":
        document.verification_status = "verified"
        document.verified_by_id = current_user.id
        document.verified_at = utcnow()
        flash("Document verified.", "success")
    elif decision == "reject":
        document.verification_status = "rejected"
        document.verified_by_id = current_user.id
        document.verified_at = utcnow()
        document.rejection_reason = request.form.get("rejection_reason")
        flash("Document rejected.", "info")
    else:
        abort(400)
    log_action("document_verification", "Document", document.id,
               new_value={"status": document.verification_status})
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard.home"))
