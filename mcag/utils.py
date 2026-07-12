"""Shared helpers: money formatting, decimals, dates, validation."""
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")


def D(value) -> Decimal:
    """Coerce a value to Decimal safely (never through float repr surprises)."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value) -> Decimal:
    """Quantize to 2 decimal places, banker-safe HALF_UP."""
    return D(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def format_cedi(value) -> str:
    """Format an amount as GH¢1,234.56."""
    amount = money(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}GH¢{abs(amount):,.2f}"


def format_date_gh(value) -> str:
    """Ghanaian date format: dd/mm/yyyy."""
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def calculate_age(dob: date, as_of: date | None = None) -> int | None:
    if not dob:
        return None
    as_of = as_of or date.today()
    return as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))


GH_PHONE_RE = re.compile(r"^(\+233|0)\d{9}$")


def valid_gh_phone(value: str) -> bool:
    return bool(GH_PHONE_RE.match((value or "").replace(" ", "")))


def normalize_phone(value: str) -> str:
    """Normalize Ghanaian phone numbers to 0XXXXXXXXX form for matching."""
    v = (value or "").replace(" ", "").replace("-", "")
    if v.startswith("+233"):
        v = "0" + v[4:]
    elif v.startswith("233") and len(v) == 12:
        v = "0" + v[3:]
    return v


GHANA_CARD_RE = re.compile(r"^GHA-\d{9}-\d$", re.IGNORECASE)


def valid_ghana_card(value: str) -> bool:
    return bool(GHANA_CARD_RE.match((value or "").strip()))


def mask_ghana_card(value: str) -> str:
    """Mask a Ghana Card number: GHA-*****1234-5."""
    v = (value or "").strip()
    if len(v) < 6:
        return "***"
    return v[:4] + "*****" + v[-6:]


def password_strength_errors(password: str, min_length: int = 10) -> list[str]:
    errors = []
    if len(password or "") < min_length:
        errors.append(f"Password must be at least {min_length} characters long.")
    if not re.search(r"[A-Z]", password or ""):
        errors.append("Password must contain an uppercase letter.")
    if not re.search(r"[a-z]", password or ""):
        errors.append("Password must contain a lowercase letter.")
    if not re.search(r"\d", password or ""):
        errors.append("Password must contain a digit.")
    if not re.search(r"[^A-Za-z0-9]", password or ""):
        errors.append("Password must contain a special character.")
    return errors


def next_number(prefix: str, last: int) -> str:
    """Sequential document numbers, e.g. RCP-000123."""
    return f"{prefix}-{last + 1:06d}"
