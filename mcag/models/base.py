from datetime import datetime, timezone

from sqlalchemy.orm import declared_attr

from mcag.extensions import db


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class TenantMixin:
    """Every institution-owned record carries institution_id.

    All queries against these models MUST be scoped through the
    mcag.services.tenancy helpers so one institution can never read
    another institution's records.
    """

    @declared_attr
    def institution_id(cls):
        return db.Column(
            db.Integer,
            db.ForeignKey("institutions.id"),
            nullable=False,
            index=True,
        )
