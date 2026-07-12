"""MCAG Loan Management System — application entry point.

Local development:   python app.py
Production (Render): gunicorn app:app
"""
import os

from mcag import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
