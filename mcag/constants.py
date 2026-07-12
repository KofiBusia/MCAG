"""Domain constants: roles, permissions, statuses, chart of accounts."""

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_INSTITUTION_ADMIN = "institution_admin"
ROLE_PROPRIETOR = "proprietor"
ROLE_MANAGER = "manager"
ROLE_CREDIT_OFFICER = "credit_officer"
ROLE_LOAN_OFFICER = "loan_officer"
ROLE_ACCOUNTS_OFFICER = "accounts_officer"
ROLE_CASHIER = "cashier"
ROLE_RECOVERY_OFFICER = "recovery_officer"
ROLE_COMPLIANCE_OFFICER = "compliance_officer"
ROLE_INTERNAL_AUDITOR = "internal_auditor"
ROLE_REVIEWER = "reviewer"

INSTITUTION_ROLES = [
    (ROLE_INSTITUTION_ADMIN, "Institution Administrator"),
    (ROLE_PROPRIETOR, "Proprietor / CEO"),
    (ROLE_MANAGER, "Manager"),
    (ROLE_CREDIT_OFFICER, "Credit Officer"),
    (ROLE_LOAN_OFFICER, "Loan Officer"),
    (ROLE_ACCOUNTS_OFFICER, "Accounts Officer"),
    (ROLE_CASHIER, "Cashier"),
    (ROLE_RECOVERY_OFFICER, "Recovery Officer"),
    (ROLE_COMPLIANCE_OFFICER, "Compliance Officer"),
    (ROLE_INTERNAL_AUDITOR, "Internal Auditor"),
    (ROLE_REVIEWER, "Read-only Reviewer"),
]

ROLE_LABELS = dict(INSTITUTION_ROLES + [(ROLE_PLATFORM_ADMIN, "Platform Super Administrator")])

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
P_VIEW = "view"
P_CREATE = "create"
P_EDIT = "edit"
P_RECOMMEND = "recommend"
P_APPROVE = "approve"
P_DISBURSE = "disburse"
P_RECEIVE_REPAYMENT = "receive_repayment"
P_REVERSE_PAYMENT = "reverse_payment"
P_WAIVE_CHARGES = "waive_charges"
P_RESTRUCTURE = "restructure"
P_WRITE_OFF = "write_off"
P_EXPORT = "export_reports"
P_VIEW_AUDIT = "view_audit_logs"
P_MANAGE_USERS = "manage_users"
P_MANAGE_SETTINGS = "manage_settings"
P_POST_JOURNAL = "post_journal"
P_MANAGE_PRODUCTS = "manage_products"
P_FIELD_VERIFY = "field_verify"
P_ASSESS = "assess"
P_RECOVERY = "recovery"
P_COMPLIANCE = "compliance"
P_INSPECT = "inspect"

ALL_PERMISSIONS = [
    P_VIEW, P_CREATE, P_EDIT, P_RECOMMEND, P_APPROVE, P_DISBURSE,
    P_RECEIVE_REPAYMENT, P_REVERSE_PAYMENT, P_WAIVE_CHARGES, P_RESTRUCTURE,
    P_WRITE_OFF, P_EXPORT, P_VIEW_AUDIT, P_MANAGE_USERS, P_MANAGE_SETTINGS,
    P_POST_JOURNAL, P_MANAGE_PRODUCTS, P_FIELD_VERIFY, P_ASSESS, P_RECOVERY,
    P_COMPLIANCE, P_INSPECT,
]

ROLE_PERMISSIONS = {
    ROLE_INSTITUTION_ADMIN: set(ALL_PERMISSIONS),
    ROLE_PROPRIETOR: {
        P_VIEW, P_APPROVE, P_WAIVE_CHARGES, P_RESTRUCTURE, P_WRITE_OFF,
        P_EXPORT, P_VIEW_AUDIT, P_MANAGE_USERS, P_MANAGE_SETTINGS,
        P_MANAGE_PRODUCTS, P_INSPECT, P_COMPLIANCE,
    },
    ROLE_MANAGER: {
        P_VIEW, P_CREATE, P_EDIT, P_RECOMMEND, P_APPROVE, P_DISBURSE,
        P_WAIVE_CHARGES, P_RESTRUCTURE, P_EXPORT, P_VIEW_AUDIT,
        P_MANAGE_PRODUCTS, P_ASSESS, P_FIELD_VERIFY, P_INSPECT,
    },
    ROLE_CREDIT_OFFICER: {P_VIEW, P_CREATE, P_EDIT, P_RECOMMEND, P_ASSESS, P_FIELD_VERIFY},
    ROLE_LOAN_OFFICER: {P_VIEW, P_CREATE, P_EDIT, P_FIELD_VERIFY, P_RECEIVE_REPAYMENT},
    ROLE_ACCOUNTS_OFFICER: {
        P_VIEW, P_CREATE, P_DISBURSE, P_RECEIVE_REPAYMENT, P_POST_JOURNAL, P_EXPORT,
    },
    ROLE_CASHIER: {P_VIEW, P_RECEIVE_REPAYMENT},
    ROLE_RECOVERY_OFFICER: {P_VIEW, P_RECOVERY},
    ROLE_COMPLIANCE_OFFICER: {P_VIEW, P_COMPLIANCE, P_VIEW_AUDIT, P_EXPORT, P_INSPECT},
    ROLE_INTERNAL_AUDITOR: {P_VIEW, P_VIEW_AUDIT, P_EXPORT, P_INSPECT},
    ROLE_REVIEWER: {P_VIEW},
}

# ---------------------------------------------------------------------------
# Loan application statuses
# ---------------------------------------------------------------------------
APP_DRAFT = "Draft"
APP_SUBMITTED = "Submitted"
APP_KYC_REVIEW = "KYC Review"
APP_AWAITING_DOCS = "Awaiting Documents"
APP_FIELD_VERIFICATION = "Field Verification"
APP_CREDIT_ASSESSMENT = "Credit Assessment"
APP_RECOMMENDED = "Recommended"
APP_APPROVED = "Approved"
APP_APPROVED_CONDITIONS = "Approved With Conditions"
APP_DEFERRED = "Deferred"
APP_DECLINED = "Declined"
APP_WITHDRAWN = "Withdrawn"
APP_EXPIRED = "Expired"
APP_OFFER_ISSUED = "Offer Issued"
APP_OFFER_ACCEPTED = "Offer Accepted"
APP_DOCS_COMPLETED = "Documentation Completed"
APP_READY_DISBURSE = "Ready for Disbursement"
APP_DISBURSED = "Disbursed"

APPLICATION_STATUSES = [
    APP_DRAFT, APP_SUBMITTED, APP_KYC_REVIEW, APP_AWAITING_DOCS,
    APP_FIELD_VERIFICATION, APP_CREDIT_ASSESSMENT, APP_RECOMMENDED,
    APP_APPROVED, APP_APPROVED_CONDITIONS, APP_DEFERRED, APP_DECLINED,
    APP_WITHDRAWN, APP_EXPIRED, APP_OFFER_ISSUED, APP_OFFER_ACCEPTED,
    APP_DOCS_COMPLETED, APP_READY_DISBURSE, APP_DISBURSED,
]

# Instalment statuses
INST_NOT_DUE = "Not Yet Due"
INST_DUE = "Due"
INST_PARTLY_PAID = "Partly Paid"
INST_PAID = "Paid"
INST_OVERDUE = "Overdue"
INST_RESCHEDULED = "Rescheduled"
INST_WRITTEN_OFF = "Written Off"

# Loan statuses
LOAN_ACTIVE = "Active"
LOAN_CLOSED = "Closed"
LOAN_WRITTEN_OFF = "Written Off"
LOAN_RESTRUCTURED = "Restructured"

# Interest methods
METHOD_FLAT = "flat"
METHOD_REDUCING = "reducing_balance"
INTEREST_METHODS = [(METHOD_FLAT, "Flat rate"), (METHOD_REDUCING, "Reducing balance")]

# Schedule types
SCHED_EQUAL_INSTALMENT = "equal_instalment"
SCHED_EQUAL_PRINCIPAL = "equal_principal"
SCHED_BULLET = "bullet"
SCHEDULE_TYPES = [
    (SCHED_EQUAL_INSTALMENT, "Equal instalments"),
    (SCHED_EQUAL_PRINCIPAL, "Equal principal"),
    (SCHED_BULLET, "Bullet repayment"),
]

FREQ_WEEKLY = "weekly"
FREQ_FORTNIGHTLY = "fortnightly"
FREQ_MONTHLY = "monthly"
FREQUENCIES = [
    (FREQ_WEEKLY, "Weekly"),
    (FREQ_FORTNIGHTLY, "Fortnightly"),
    (FREQ_MONTHLY, "Monthly"),
]
PERIODS_PER_YEAR = {FREQ_WEEKLY: 52, FREQ_FORTNIGHTLY: 26, FREQ_MONTHLY: 12}

PAYMENT_METHODS = [
    ("cash", "Cash"),
    ("mobile_money", "Mobile Money"),
    ("bank_transfer", "Bank Transfer"),
    ("cheque", "Cheque"),
    ("employer_deduction", "Employer Deduction"),
    ("standing_order", "Standing Order"),
]

DISBURSEMENT_METHODS = [
    ("bank_transfer", "Bank Transfer"),
    ("mobile_money", "Mobile Money"),
    ("cheque", "Cheque"),
    ("cash", "Cash"),
]

COLLATERAL_TYPES = [
    ("guarantee_fund", "Guarantee Fund"),
    ("liquid_investment", "Liquid Investment"),
    ("guarantor", "Guarantor"),
    ("landed_property", "Landed Property"),
    ("vehicle", "Vehicle"),
    ("other", "Other Approved Collateral"),
]

DOCUMENT_TYPES = [
    "Ghana Card", "Passport Photograph", "Signature", "Thumbprint",
    "Utility Bill", "Tenancy Agreement", "Business Permit", "Tax Receipt",
    "Sales Records", "Invoice", "Bank Statement", "Mobile Money Statement",
    "Payslip", "Employer Undertaking", "Guarantor Document",
    "Collateral Document", "Credit Bureau Report", "Field Visit Photograph",
    "Signed Offer Letter", "Signed Loan Agreement", "Signed Guarantor Agreement",
    "Regulatory Document", "Proof of MCAG Submission", "Other",
]

# Arrears buckets: (key, label, min_days, max_days)
ARREARS_BUCKETS = [
    ("current", "Current", 0, 0),
    ("d1_7", "1 - 7 days", 1, 7),
    ("d8_30", "8 - 30 days", 8, 30),
    ("d31_60", "31 - 60 days", 31, 60),
    ("d61_90", "61 - 90 days", 61, 90),
    ("d91_180", "91 - 180 days", 91, 180),
    ("d180_plus", "Above 180 days", 181, 100000),
]

# Default provisioning rates (percent of outstanding principal) — configurable
# per institution in institution settings.
DEFAULT_PROVISION_RATES = {
    "current": "1",
    "d1_7": "5",
    "d8_30": "10",
    "d31_60": "25",
    "d61_90": "50",
    "d91_180": "75",
    "d180_plus": "100",
}

# Sector list matches the MCAG Members Reporting Template (MRT) sectorial
# breakdown so returns can be generated without re-mapping.
LOAN_PURPOSE_SECTORS = [
    "Agric / fishing / forestry",
    "Manufacturing / Agro-processing",
    "Commerce / Trading",
    "Education",
    "Church / Religious related enterprise",
    "Transport / Communication",
    "Services",
    "Household Consumption",
    "Mortgage / housing / home improvement",
    "Funeral / Festive Occasions",
    "Mining & Quarrying",
    "Construction",
    "Others",
]

# MRT portfolio ageing buckets: (key, label, min_days, max_days, min_prov_rate%)
MRT_AGEING_BUCKETS = [
    ("current", "CURRENT", 0, 0, "1"),
    ("d1_30", "1-30 DAYS", 1, 30, "5"),
    ("d31_60", "31-60 DAYS", 31, 60, "20"),
    ("d61_90", "61 - 90 DAYS", 61, 90, "40"),
    ("d91_120", "91 - 120 DAYS", 91, 120, "60"),
    ("d121_150", "121 - 150 DAYS", 121, 150, "80"),
    ("d151_180", "151 - 180 DAYS", 151, 180, "100"),
    ("d181_365", "181 - 365 days", 181, 365, "100"),
    ("d365_plus", "> 365 days", 366, 100000, "100"),
]

MRT_REGION_CODES = {
    "Ahafo": "Ahafo-AF", "Ashanti": "Ashanti-AS", "Bono": "Bono-BN",
    "Bono East": "Bono East-BE", "Central": "Central-CR", "Eastern": "Eastern-ER",
    "Greater Accra": "Greater Accra-GA", "North East": "North East-NE",
    "Northern": "Northern-NR", "Oti": "Oti-OT", "Savannah": "Savannah-SV",
    "Upper East": "Upper East-UE", "Upper West": "Upper West-UW",
    "Volta": "Volta-VR", "Western": "Western-WR", "Western North": "Western North-WN",
}

GHANA_REGIONS = [
    "Greater Accra", "Ashanti", "Western", "Western North", "Central",
    "Eastern", "Volta", "Oti", "Northern", "Savannah", "North East",
    "Upper East", "Upper West", "Bono", "Bono East", "Ahafo",
]

COMPLAINT_CHANNELS = [
    ("office_visit", "Office Visit"), ("telephone", "Telephone"),
    ("letter", "Letter"), ("email", "Email"),
    ("mcag_referral", "MCAG Referral"), ("regulator_referral", "Regulator Referral"),
]

FUNDING_SOURCES = [
    ("owner_capital", "Owner Capital"),
    ("shareholder", "Shareholder Funding"),
    ("bank_borrowing", "Bank Borrowing"),
    ("wholesale_borrowing", "Wholesale Borrowing"),
    ("donor", "Donor Funding"),
    ("retained_earnings", "Retained Earnings"),
    ("other", "Other Approved Funding"),
]

# ---------------------------------------------------------------------------
# Default chart of accounts: (code, name, type, subtype)
# type: asset | liability | equity | income | expense
# ---------------------------------------------------------------------------
DEFAULT_CHART_OF_ACCOUNTS = [
    ("1000", "Cash on Hand", "asset", "cash"),
    ("1010", "Bank Account", "asset", "bank"),
    ("1100", "Gross Loan Portfolio", "asset", "portfolio"),
    ("1110", "Interest Receivable", "asset", "interest_receivable"),
    ("1190", "Loan-Loss Provision (Contra)", "asset", "provision"),
    ("1500", "Fixed Assets", "asset", "fixed_asset"),
    ("2000", "Borrowings", "liability", "borrowings"),
    ("2100", "Payables", "liability", "payables"),
    ("3000", "Capital", "equity", "capital"),
    ("3100", "Retained Earnings", "equity", "retained_earnings"),
    ("4000", "Interest Income", "income", "interest_income"),
    ("4100", "Fee Income", "income", "fee_income"),
    ("4200", "Penalty Income", "income", "penalty_income"),
    ("4300", "Recovery Income", "income", "recovery_income"),
    ("5000", "Staff Costs", "expense", "staff_costs"),
    ("5100", "Rent", "expense", "rent"),
    ("5200", "Utilities", "expense", "utilities"),
    ("5300", "Transport", "expense", "transport"),
    ("5400", "Provision Expense", "expense", "provision_expense"),
    ("5500", "Bad-Debt Expense", "expense", "bad_debt"),
    ("5900", "Other Operating Expenses", "expense", "other_expense"),
]

RISK_RATINGS = ["Low", "Moderate", "High", "Very High"]

ALERT_TYPES = {
    "duplicate_ghana_card": "Duplicate Ghana Card",
    "duplicate_phone": "Duplicate telephone number",
    "duplicate_bank_account": "Duplicate bank account",
    "duplicate_momo": "Duplicate mobile money number",
    "duplicate_document": "Duplicate document (same file hash)",
    "existing_active_loan": "Customer has an existing active loan",
    "written_off_history": "Customer has a written-off loan",
    "customer_is_guarantor": "Customer already acting as guarantor",
    "guarantor_many_borrowers": "Guarantor linked to many borrowers",
    "staff_phone_match": "Staff phone number in customer record",
    "staff_bank_match": "Staff bank account in customer record",
    "shared_payment_account": "Several customers using the same payment account",
    "backdated_transaction": "Repeated backdated transactions",
    "unusual_reversal": "Unusual payment reversal",
    "rate_change_after_approval": "Interest rate changed after approval",
    "approval_outside_limit": "Loan approved outside authority limit",
    "fast_disbursement": "Loan disbursed immediately after customer creation",
    "shared_collateral": "Customers linked to the same collateral",
}
