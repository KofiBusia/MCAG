"""MCAG Loan Management System — application factory."""
import logging
import sys

from flask import Flask, jsonify, redirect, url_for
from flask_login import current_user
from sqlalchemy import text

from mcag.config import get_config
from mcag.extensions import csrf, db, login_manager, migrate


def create_app(config_object=None):
    app = Flask(__name__)
    app.config.from_object(config_object or get_config())

    if not app.config.get("SECRET_KEY"):
        if app.config.get("FLASK_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production.")
        app.config["SECRET_KEY"] = "dev-only-secret-key-change-me"

    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from mcag import models  # noqa: F401 — register models with SQLAlchemy

    _register_blueprints(app)
    _register_filters(app)
    _register_security(app)
    _register_health(app)
    _register_cli(app)
    _register_errors(app)

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            if current_user.is_platform_admin:
                return redirect(url_for("platform_admin.dashboard"))
            return redirect(url_for("dashboard.home"))
        return redirect(url_for("auth.login"))

    return app


def _configure_logging(app):
    # Log to stdout — required for Render's log streaming.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
    app.logger.setLevel(level)
    if not app.logger.handlers:
        app.logger.addHandler(handler)


def _register_blueprints(app):
    from mcag.blueprints.auth import bp as auth_bp
    from mcag.blueprints.dashboard import bp as dashboard_bp
    from mcag.blueprints.platform_admin import bp as platform_bp
    from mcag.blueprints.institution import bp as institution_bp
    from mcag.blueprints.customers import bp as customers_bp
    from mcag.blueprints.products import bp as products_bp
    from mcag.blueprints.applications import bp as applications_bp
    from mcag.blueprints.loans import bp as loans_bp
    from mcag.blueprints.collections_ import bp as collections_bp
    from mcag.blueprints.arrears import bp as arrears_bp
    from mcag.blueprints.accounting import bp as accounting_bp
    from mcag.blueprints.compliance import bp as compliance_bp
    from mcag.blueprints.reports import bp as reports_bp
    from mcag.blueprints.documents import bp as documents_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(platform_bp, url_prefix="/platform")
    app.register_blueprint(institution_bp, url_prefix="/institution")
    app.register_blueprint(customers_bp, url_prefix="/customers")
    app.register_blueprint(products_bp, url_prefix="/products")
    app.register_blueprint(applications_bp, url_prefix="/applications")
    app.register_blueprint(loans_bp, url_prefix="/loans")
    app.register_blueprint(collections_bp, url_prefix="/collections")
    app.register_blueprint(arrears_bp, url_prefix="/arrears")
    app.register_blueprint(accounting_bp, url_prefix="/accounting")
    app.register_blueprint(compliance_bp, url_prefix="/compliance")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(documents_bp, url_prefix="/documents")


def _register_filters(app):
    from mcag.utils import format_cedi, format_date_gh, mask_ghana_card

    app.jinja_env.filters["cedi"] = format_cedi
    app.jinja_env.filters["ghdate"] = format_date_gh
    app.jinja_env.filters["mask_card"] = mask_ghana_card

    from mcag import constants

    @app.context_processor
    def inject_globals():
        return {"C": constants, "APP_NAME": app.config["APP_NAME"]}


def _register_security(app):
    from flask import request, session

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.before_request
    def enforce_https():
        # Behind Render's proxy X-Forwarded-Proto carries the original scheme.
        if (app.config.get("FLASK_ENV") == "production"
                and not app.config.get("TESTING")
                and request.headers.get("X-Forwarded-Proto", "https") == "http"):
            url = request.url.replace("http://", "https://", 1)
            return redirect(url, code=301)

    @app.before_request
    def refresh_session():
        session.permanent = True


def _register_health(app):
    @app.route("/healthz")
    def healthz():
        try:
            db.session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # pragma: no cover
            db_ok = False
        status = 200 if db_ok else 503
        return jsonify({
            "status": "ok" if db_ok else "degraded",
            "database": "connected" if db_ok else "unavailable",
        }), status


def _register_errors(app):
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _register_cli(app):
    from mcag.cli import register_cli
    register_cli(app)
