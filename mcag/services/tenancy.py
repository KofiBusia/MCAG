"""Tenant isolation helpers.

EVERY query on institution-owned data must go through these helpers (or
explicitly filter institution_id). Record lookups by id use
get_tenant_or_404 so changing a URL id can never expose another
institution's record.
"""
from flask import abort
from flask_login import current_user

from mcag.extensions import db


def current_institution_id() -> int:
    if not current_user.is_authenticated or current_user.institution_id is None:
        abort(403)
    return current_user.institution_id


def tenant_query(model):
    """A query pre-filtered to the current user's institution."""
    return model.query.filter(model.institution_id == current_institution_id())


def get_tenant_or_404(model, record_id: int):
    """Fetch a record by id, returning 404 unless it belongs to the
    current user's institution (indistinguishable from non-existence)."""
    record = db.session.get(model, record_id)
    if record is None or record.institution_id != current_institution_id():
        abort(404)
    return record


def stamp_tenant(record):
    """Force the institution id from the session — never from form input."""
    record.institution_id = current_institution_id()
    return record
