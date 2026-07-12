"""Shared route decorators and pagination helpers."""
from functools import wraps

from flask import abort, request
from flask_login import current_user, login_required

from mcag.constants import ROLE_PLATFORM_ADMIN


def permission_required(*permissions):
    """Require an institution user holding ALL listed permissions."""
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.is_platform_admin:
                # Platform admins do not operate on institution records.
                abort(403)
            if current_user.institution_id is None:
                abort(403)
            if current_user.institution.status != "active":
                abort(403)
            for perm in permissions:
                if not current_user.can(perm):
                    abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def platform_admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.role != ROLE_PLATFORM_ADMIN:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def page_args(default_per_page=25):
    page = request.args.get("page", 1, type=int)
    per_page = min(request.args.get("per_page", default_per_page, type=int), 100)
    return page, per_page
