"""Append-only audit trail service."""
import json

from flask import has_request_context, request
from flask_login import current_user

from mcag.extensions import db
from mcag.models.compliance import AuditLog


def _serialize(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def log_action(action: str, record_type: str = None, record_id=None,
               old_value=None, new_value=None, institution_id=None, user=None):
    """Write an immutable audit record. Never raises into business flow."""
    actor = user
    if actor is None and current_user and getattr(current_user, "is_authenticated", False):
        actor = current_user
    entry = AuditLog(
        institution_id=institution_id if institution_id is not None else (
            getattr(actor, "institution_id", None) if actor else None),
        user_id=getattr(actor, "id", None) if actor else None,
        user_email=getattr(actor, "email", None) if actor else None,
        action=action,
        record_type=record_type,
        record_id=str(record_id) if record_id is not None else None,
        old_value=_serialize(old_value),
        new_value=_serialize(new_value),
        ip_address=request.remote_addr if has_request_context() else None,
        session_info=(request.user_agent.string[:250]
                      if has_request_context() and request.user_agent else None),
    )
    db.session.add(entry)
    return entry
