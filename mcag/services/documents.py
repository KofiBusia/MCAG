"""Secure document storage: random storage keys, per-tenant folders,
SHA-256 hashing, authenticated download only."""
import hashlib
import os
import secrets

from flask import current_app
from werkzeug.utils import secure_filename

from mcag.extensions import db
from mcag.models import Document
from mcag.services.audit import log_action


class DocumentError(ValueError):
    pass


def _upload_root() -> str:
    root = current_app.config["UPLOAD_FOLDER"]
    if not os.path.isabs(root):
        root = os.path.join(current_app.root_path, "..", root)
    return os.path.abspath(root)


def storage_path(document: Document) -> str:
    return os.path.join(_upload_root(), f"institution_{document.institution_id}",
                        document.storage_key)


def allowed_file(filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_UPLOAD_EXTENSIONS"]


def save_upload(file_storage, institution_id: int, document_type: str, user,
                customer_id=None, application_id=None, loan_id=None,
                guarantor_id=None, collateral_id=None, expiry_date=None,
                immutable=False) -> Document:
    filename = secure_filename(file_storage.filename or "")
    if not filename:
        raise DocumentError("No file selected.")
    if not allowed_file(filename):
        raise DocumentError("File type not allowed.")

    data = file_storage.read()
    if not data:
        raise DocumentError("Uploaded file is empty.")
    sha256 = hashlib.sha256(data).hexdigest()
    ext = filename.rsplit(".", 1)[-1].lower()
    storage_key = f"{secrets.token_urlsafe(24)}.{ext}"

    folder = os.path.join(_upload_root(), f"institution_{institution_id}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, storage_key), "wb") as fh:
        fh.write(data)

    document = Document(
        institution_id=institution_id,
        storage_key=storage_key,
        original_filename=filename,
        document_type=document_type,
        content_type=file_storage.content_type,
        size_bytes=len(data),
        sha256=sha256,
        customer_id=customer_id,
        loan_application_id=application_id,
        loan_id=loan_id,
        guarantor_id=guarantor_id,
        collateral_id=collateral_id,
        uploaded_by_id=user.id,
        expiry_date=expiry_date,
        immutable=immutable,
    )
    db.session.add(document)
    db.session.flush()
    log_action("document_uploaded", "Document", document.id,
               new_value={"type": document_type, "filename": filename, "sha256": sha256})
    from mcag.services.alerts import scan_document_hash
    scan_document_hash(document)
    return document


def save_generated_pdf(pdf_bytes: bytes, institution_id: int, document_type: str,
                       user, filename: str, **links) -> Document:
    """Persist a system-generated PDF (offer letter, agreement…) immutably."""
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    storage_key = f"{secrets.token_urlsafe(24)}.pdf"
    folder = os.path.join(_upload_root(), f"institution_{institution_id}")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, storage_key), "wb") as fh:
        fh.write(pdf_bytes)
    document = Document(
        institution_id=institution_id,
        storage_key=storage_key,
        original_filename=filename,
        document_type=document_type,
        content_type="application/pdf",
        size_bytes=len(pdf_bytes),
        sha256=sha256,
        uploaded_by_id=user.id,
        immutable=True,
        verification_status="verified",
        **links,
    )
    db.session.add(document)
    db.session.flush()
    log_action("document_generated", "Document", document.id,
               new_value={"type": document_type, "filename": filename})
    return document
