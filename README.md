# MCAG Loan Management System

A secure, **staff-only** loan administration, accounting, compliance and
reporting platform for **Ghanaian Micro-Credit Enterprises**, built around the
official MCAG (Micro-Credit Association Ghana) templates:

1. Loan Application Form
2. Loan Offer Letter
3. Loan Agreement
4. MCAG Members Reporting Template (MRT V2022 v8)

The original source documents live in [`source_documents/`](source_documents/)
and drive the system's forms, generated documents and the Excel return export.

## What this system is — and is not

**It is** a multi-tenant back-office for registered microcredit enterprises:
customer KYC, loan applications, field verification, credit assessment,
maker-checker approvals, offer letters and agreements (PDF + Word), a
Decimal-precise loan calculation engine, disbursements, repayment collection
with sequential receipts, arrears/PAR/provisioning, double-entry accounting,
complaints, credit bureau register, fraud/duplicate alerts, data protection
registers, a tamper-resistant audit trail, and automatic MCAG MRT returns.

**It is not** a customer lending app. Customers never log in. There are no
savings, deposits, wallets, customer transfers, public loan applications,
automatic AI approvals, or borrower-shaming/publication features. Collection
zones are field operational areas — **not branches**.

## Technology

| Layer | Choice |
|---|---|
| Language | Python 3.11 |
| Framework | Flask 3 (app factory, blueprints) |
| ORM / DB | SQLAlchemy 2 + PostgreSQL (SQLite fallback for dev) |
| Migrations | Flask-Migrate (Alembic) |
| Auth | Flask-Login, hashed passwords, lockout, CSRF (Flask-WTF) |
| Money | `decimal.Decimal` + `Numeric(18,2)` — no floats, ever |
| PDF | xhtml2pdf |
| Word | python-docx (fill electronically or print blank forms) |
| Excel | openpyxl (fills the official MRT workbook, formulas intact) |
| Server | gunicorn (Render) / `python app.py` (local) |
| Tests | pytest |

## Local setup

```bash
git clone https://github.com/KofiBusia/MCAG.git
cd MCAG
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env      # (cp on macOS/Linux) then edit values
```

### Database setup

**Option A — PostgreSQL (recommended, matches production):**

```bash
createdb mcag
# in .env:
# DATABASE_URL=postgresql://username:password@localhost:5432/mcag
flask db upgrade
```

**Option B — quick start (SQLite):** leave `DATABASE_URL` unset; a local
`mcag_dev.sqlite3` file is used automatically. Run `flask db upgrade`.

### Create the platform administrator (required once)

```bash
flask create-platform-admin
# or non-interactively:
# set PLATFORM_ADMIN_EMAIL / PLATFORM_ADMIN_PASSWORD in the environment first
```

No default admin or predictable password is ever created. The administrator
must change the password at first login.

### Seed development data (optional, dev only)

```bash
flask seed-dev
```

Creates “Demo Microcredit Enterprise” with staff (password `DemoPass123!`):
`admin@demo-mce.example`, `manager@demo-mce.example`,
`officer@demo-mce.example`, `accounts@demo-mce.example`, the five MCAG loan
products, a collection zone and sample customers. Refuses to run when
`FLASK_ENV=production`.

### Run

```bash
python app.py
# → http://localhost:5000  (health check at /healthz)
```

## Migrations

```bash
flask db migrate -m "describe change"   # generate after model changes
flask db upgrade                        # apply
flask db downgrade                      # roll back one revision
```

The initial migration is committed under `migrations/versions/`. Production
deploys run `flask db upgrade` automatically before start.

## Testing

```bash
pytest            # full suite
pytest tests/test_loan_engine.py -v
```

The suite covers authentication and lockout, permissions, **tenant isolation**
(mandatory), the loan calculation engine (flat/reducing, equal
instalment/principal/bullet, rounding into the final instalment, the GH¢43×13
sample-document error case), repayment schedules, approval workflow with
maker-checker, disbursement controls, repayment allocation and reversal,
arrears classification and provisioning, accounting entries, MCAG report
generation and validation, document generation (PDF/Word/Excel) and audit
logging.

## Deployment on Render

The repo includes [`render.yaml`](render.yaml) (Blueprint) and a `Procfile`.

**One-click blueprint:** push to GitHub → Render Dashboard → *New → Blueprint*
→ select this repository. It provisions the web service, a PostgreSQL
database, a persistent disk for uploads, and wires `DATABASE_URL` and a
generated `SECRET_KEY` automatically.

**Manual service:**

| Setting | Value |
|---|---|
| Build command | `pip install -r requirements.txt` |
| Start command | `flask db upgrade && gunicorn app:app --workers 2 --timeout 120 --log-file -` |
| Health check | `/healthz` |

Required production environment variables (all read from the environment —
nothing is hard-coded): `FLASK_ENV=production`, `SECRET_KEY`, `DATABASE_URL`,
`SESSION_COOKIE_SECURE=true`, plus the optional variables listed in
[`.env.example`](.env.example). `PORT` is provided by Render and honoured by
both gunicorn and `python app.py`.

After the first deploy, open a Render shell and run:

```bash
flask create-platform-admin
```

Production hardening is automatic when `FLASK_ENV=production`: debug off,
HTTPS redirect (via `X-Forwarded-Proto`), HSTS, secure/HttpOnly/SameSite
cookies, session timeout, account lockout, and startup fails fast if
`SECRET_KEY` is missing.

## Key design guarantees

- **Financial figures are never typed.** Interest, totals, instalments, APR
  and balances come only from the central calculation engine
  (`mcag/services/loan_engine.py`), which validates that instalments sum to
  the total repayment, the final balance is exactly zero, and rounding
  differences land in the final instalment. The inconsistent sample offer
  (GH¢2,500 + GH¢600 ≠ 13 × GH¢43) cannot be reproduced.
- **Tenant isolation.** Every institution-owned row carries
  `institution_id`; all queries pass through `mcag/services/tenancy.py`, and
  record lookups 404 across tenants. Covered by mandatory tests.
- **Maker-checker.** Creation/approval/disbursement, reversals, waivers,
  write-offs, restructures and cashbook approval each require two different
  users.
- **Auditability.** Append-only audit trail (no update/delete routes),
  sequential receipts that are never deleted, immutable offer/agreement
  records, hashed document storage with random keys behind authenticated,
  tenant-checked downloads.
- **MCAG MRT.** Returns are computed from the ledger/journals and exported by
  filling the genuine `MCAG-MRT_V2022_v8` workbook so the association's own
  validation formulas keep working; periods can be locked and the submitted
  version is stored verbatim.

## Repository layout

```
app.py                  # entry point (python app.py / gunicorn app:app)
mcag/
  config.py             # env-driven configuration (dev/test/production)
  constants.py          # roles, permissions, statuses, chart of accounts, MRT
  models/               # SQLAlchemy models (all tenant-scoped)
  services/             # loan engine, tenancy, accounting, arrears, alerts,
                        # audit, documents, PDF, Word, MCAG report
  blueprints/           # auth, dashboard, platform admin, institution,
                        # customers, products, applications, loans,
                        # collections, arrears, accounting, compliance,
                        # reports, documents
  templates/ static/    # UI + PDF templates (GH¢ formatting, dd/mm/yyyy)
migrations/             # Alembic migrations
source_documents/       # official MCAG templates (basis of the system)
tests/                  # pytest suite (tenant isolation mandatory)
render.yaml Procfile    # Render deployment
```
