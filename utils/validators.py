"""
Input Validators
=================
Validation functions for all inbound data to the middleware.
Validates structure, types and business rules before any data
reaches SoftLedger.

Defence-in-depth: validate everything, trust nothing from outside.
"""

from datetime import datetime
from typing import List, Optional


def validate_journal_entry(data: dict) -> List[str]:
    """
    Validate a journal entry payload.
    Returns a list of error messages (empty list = valid).
    """
    errors = []

    # Required fields
    if not data.get("date"):
        errors.append("'date' is required")
    else:
        try:
            datetime.strptime(str(data["date"]), "%Y-%m-%d")
        except ValueError:
            errors.append("'date' must be in YYYY-MM-DD format")

    if not data.get("location_id"):
        errors.append("'location_id' is required")
    elif not isinstance(data["location_id"], int):
        errors.append("'location_id' must be an integer")

    if not data.get("lines"):
        errors.append("'lines' is required and must not be empty")
    elif not isinstance(data["lines"], list):
        errors.append("'lines' must be an array")
    elif len(data["lines"]) < 2:
        errors.append("Journal entry must have at least 2 lines (double-entry)")
    else:
        for i, line in enumerate(data["lines"]):
            line_errors = validate_journal_line(line, i)
            errors.extend(line_errors)

    # Currency validation
    if data.get("currency") and data["currency"] not in VALID_CURRENCIES:
        errors.append(f"'currency' must be a valid ISO 4217 code, got: {data['currency']}")

    # Description length
    if data.get("description") and len(str(data["description"])) > 500:
        errors.append("'description' must be 500 characters or fewer")

    return errors


def validate_journal_line(line: dict, index: int) -> List[str]:
    """Validate a single journal entry line."""
    errors = []
    prefix = f"lines[{index}]"

    if not line.get("account_id"):
        errors.append(f"{prefix}: 'account_id' is required")
    elif not isinstance(line["account_id"], int):
        errors.append(f"{prefix}: 'account_id' must be an integer")

    debit = line.get("debit", 0)
    credit = line.get("credit", 0)

    try:
        debit = float(debit)
        credit = float(credit)
    except (TypeError, ValueError):
        errors.append(f"{prefix}: 'debit' and 'credit' must be numeric")
        return errors

    if debit < 0 or credit < 0:
        errors.append(f"{prefix}: 'debit' and 'credit' must be non-negative")

    if debit > 0 and credit > 0:
        errors.append(f"{prefix}: A line cannot have both debit and credit amounts")

    if debit == 0 and credit == 0:
        errors.append(f"{prefix}: A line must have either a debit or credit amount")

    if debit > 999_999_999 or credit > 999_999_999:
        errors.append(f"{prefix}: Amount exceeds maximum allowed value")

    return errors


def validate_transaction(data: dict) -> List[str]:
    """Validate an inbound webhook transaction payload."""
    errors = []

    if not data.get("date"):
        errors.append("'date' is required")
    else:
        try:
            datetime.strptime(str(data["date"]), "%Y-%m-%d")
        except ValueError:
            errors.append("'date' must be in YYYY-MM-DD format")

    if data.get("amount") is None:
        errors.append("'amount' is required")
    else:
        try:
            amount = float(data["amount"])
            if amount == 0:
                errors.append("'amount' must be non-zero")
        except (TypeError, ValueError):
            errors.append("'amount' must be numeric")

    if data.get("currency") and data["currency"] not in VALID_CURRENCIES:
        errors.append(f"'currency' must be a valid ISO 4217 code, got: {data['currency']}")

    return errors


# ISO 4217 currency codes — common subset used in international finance
VALID_CURRENCIES = {
    "GBP", "USD", "EUR", "AUD", "CAD", "NZD", "HKD", "SGD",
    "CHF", "JPY", "CNY", "SEK", "NOK", "DKK", "MXN", "BRL",
    "ZAR", "NGN", "KES", "GHS", "INR", "PKR", "BDT", "PHP",
    "THB", "MYR", "IDR", "VND", "KRW", "TWD", "AED", "SAR",
    "QAR", "KWD", "BHD", "OMR", "JOD", "EGP", "MAD", "TND",
    # Stablecoins treated as currencies in digital asset accounting
    "USDT", "USDC", "BUSD", "DAI",
}
