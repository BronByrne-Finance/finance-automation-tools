"""
Bank CSV to SoftLedger Middleware
===================================
Automatically detects, parses and normalises CSV bank statements from
multiple banking providers and prepares them for import into SoftLedger.

Supported banks:
    - Wise (TransferWise)
    - Privat Bank (Privat 3)
    - Santander UK
    - Bison Bank
    - OpenPayd
    - Barclays
    - HSBC UK
    - Finductive

Usage:
    python scripts/bank_csv_processor.py --file statement.csv
    python scripts/bank_csv_processor.py --file statement.csv --push
    python scripts/bank_csv_processor.py --file statement.csv --entity UK001
    python scripts/bank_csv_processor.py --dir ./statements/ --push
"""

import os
import sys
import csv
import json
import argparse
from datetime import datetime
from pathlib import Path
from tabulate import tabulate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.fx_rates import get_live_rates, convert_to_base, format_currency

# ─── STANDARD TRANSACTION FORMAT ──────────────────────────────────────────────
# All bank CSVs are normalised to this structure before processing

STANDARD_FIELDS = [
    "date",           # Transaction date (datetime)
    "description",    # Payee / narrative
    "reference",      # Payment reference
    "amount",         # Signed amount (positive = credit, negative = debit)
    "currency",       # ISO currency code
    "balance",        # Running balance after transaction
    "category",       # Auto-categorised type
    "bank",           # Source bank identifier
    "raw_row",        # Original row for audit trail
]

# ─── TRANSACTION CATEGORIES ───────────────────────────────────────────────────
# Keywords used to auto-categorise transactions

CATEGORY_RULES = {
    "payroll":     ["salary", "payroll", "wages", "pay run", "eor", "deel", "remote.com", "oyster"],
    "tax":         ["hmrc", "vat", "corporation tax", "paye", "irs", "tax payment", "revenue"],
    "crypto":      ["kraken", "binance", "valr", "circle", "usdc", "usdt", "coinbase", "bitpay"],
    "supplier":    ["invoice", "supplier", "vendor", "payment to"],
    "bank_fee":    ["fee", "charge", "commission", "fx fee", "transfer fee"],
    "fx":          ["exchange", "conversion", "fx", "foreign exchange", "currency"],
    "income":      ["payment from", "received from", "client payment", "revenue"],
    "intercompany":["intercompany", "group transfer", "entity transfer", "related party"],
    "rent":        ["rent", "lease", "landlord", "property"],
    "utilities":   ["electric", "gas", "water", "internet", "broadband", "utilities"],
}


# ─── BANK FORMAT DEFINITIONS ──────────────────────────────────────────────────

class BankFormat:
    """Base class for bank CSV format definitions."""

    name = "Unknown"
    signature_columns = []  # Columns that uniquely identify this bank's CSV

    @classmethod
    def detect(cls, headers: list) -> bool:
        """Return True if these headers match this bank's format."""
        headers_lower = [h.lower().strip() for h in headers]
        return all(col.lower() in headers_lower for col in cls.signature_columns)

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        """Parse a single CSV row into the standard transaction format."""
        raise NotImplementedError


class WiseFormat(BankFormat):
    name = "Wise"
    signature_columns = ["transferwise id", "amount", "exchange from", "exchange rate"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        amount_str = row.get("Amount", "0").replace(",", "").strip()
        balance_str = row.get("Running Balance", "0").replace(",", "").strip()
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Payee Name") or row.get("Description") or row.get("Merchant", ""),
            "reference": row.get("Payment Reference", ""),
            "amount": _parse_amount(amount_str),
            "currency": row.get("Currency", "GBP").strip().upper(),
            "balance": _parse_amount(balance_str),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class BarclaysFormat(BankFormat):
    name = "Barclays"
    signature_columns = ["number", "date", "account", "amount", "subcategory", "memo"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Memo", ""),
            "reference": row.get("Number", ""),
            "amount": _parse_amount(row.get("Amount", "0")),
            "currency": "GBP",
            "balance": 0.0,  # Barclays CSV does not include running balance
            "bank": cls.name,
            "raw_row": dict(row),
        }


class HSBCFormat(BankFormat):
    name = "HSBC"
    signature_columns = ["date", "type", "description", "paid out", "paid in", "balance"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        paid_in = _parse_amount(row.get("Paid In", "0").replace("£", "").strip())
        paid_out = _parse_amount(row.get("Paid Out", "0").replace("£", "").strip())
        amount = paid_in if paid_in else -paid_out
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Description", ""),
            "reference": row.get("Type", ""),
            "amount": amount,
            "currency": "GBP",
            "balance": _parse_amount(row.get("Balance", "0").replace("£", "").strip()),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class SantanderFormat(BankFormat):
    name = "Santander"
    signature_columns = ["date", "description", "amount", "balance"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Description", ""),
            "reference": "",
            "amount": _parse_amount(row.get("Amount", "0")),
            "currency": "GBP",
            "balance": _parse_amount(row.get("Balance", "0")),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class OpenPaydFormat(BankFormat):
    name = "OpenPayd"
    signature_columns = ["transaction id", "value date", "debit", "credit", "currency", "reference"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        debit = _parse_amount(row.get("Debit", "0"))
        credit = _parse_amount(row.get("Credit", "0"))
        amount = credit - debit
        return {
            "date": _parse_date(row.get("Value Date", "")),
            "description": row.get("Description", ""),
            "reference": row.get("Reference", ""),
            "amount": amount,
            "currency": row.get("Currency", "GBP").strip().upper(),
            "balance": _parse_amount(row.get("Balance", "0")),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class PrivatBankFormat(BankFormat):
    name = "Privat3"
    signature_columns = ["date", "amount", "currency", "balance", "description", "type"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Description", ""),
            "reference": row.get("Reference", row.get("ID", "")),
            "amount": _parse_amount(row.get("Amount", "0")),
            "currency": row.get("Currency", "EUR").strip().upper(),
            "balance": _parse_amount(row.get("Balance", "0")),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class BisonFormat(BankFormat):
    name = "Bison"
    signature_columns = ["date", "type", "amount", "currency", "status", "details"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        return {
            "date": _parse_date(row.get("Date", "")),
            "description": row.get("Details", row.get("Description", "")),
            "reference": row.get("Reference", ""),
            "amount": _parse_amount(row.get("Amount", "0")),
            "currency": row.get("Currency", "EUR").strip().upper(),
            "balance": _parse_amount(row.get("Balance", "0")),
            "bank": cls.name,
            "raw_row": dict(row),
        }


class FinductiveFormat(BankFormat):
    name = "Finductive"
    signature_columns = ["transaction_id", "booking_date", "amount", "currency", "label"]

    @classmethod
    def parse_row(cls, row: dict) -> dict:
        return {
            "date": _parse_date(row.get("booking_date", row.get("Booking Date", ""))),
            "description": row.get("label", row.get("Label", "")),
            "reference": row.get("transaction_id", ""),
            "amount": _parse_amount(row.get("amount", row.get("Amount", "0"))),
            "currency": row.get("currency", row.get("Currency", "EUR")).strip().upper(),
            "balance": _parse_amount(row.get("balance", row.get("Balance", "0"))),
            "bank": cls.name,
            "raw_row": dict(row),
        }


# Registry of all supported bank formats
BANK_FORMATS = [
    WiseFormat,
    HSBCFormat,         # HSBC before Barclays as it has more specific columns
    BarclaysFormat,
    SantanderFormat,
    OpenPaydFormat,
    PrivatBankFormat,
    BisonFormat,
    FinductiveFormat,
]


# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────────

def _parse_date(date_str: str) -> datetime:
    """Try multiple date formats common across banking CSV exports."""
    date_str = str(date_str).strip()
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
        "%d %b %Y", "%d %B %Y", "%Y%m%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return datetime.now()  # Fallback to today if unparseable


def _parse_amount(amount_str: str) -> float:
    """Parse an amount string, handling commas, brackets and currency symbols."""
    if not amount_str:
        return 0.0
    cleaned = str(amount_str).strip()
    # Handle bracketed negatives (1,234.56) -> -1234.56
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip()
    try:
        value = float(cleaned)
        return -value if is_negative else value
    except ValueError:
        return 0.0


def categorise_transaction(description: str, reference: str) -> str:
    """Auto-categorise a transaction based on description and reference keywords."""
    combined = f"{description} {reference}".lower()
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in combined for keyword in keywords):
            return category
    return "uncategorised"


def detect_bank_format(headers: list):
    """Detect which bank this CSV came from based on its column headers."""
    for bank_format in BANK_FORMATS:
        if bank_format.detect(headers):
            return bank_format
    return None


# ─── MAIN PROCESSOR ───────────────────────────────────────────────────────────

def process_csv(filepath: str, entity_id: str = None, base_currency: str = "GBP") -> dict:
    """
    Process a single bank CSV file.

    Returns:
        dict with keys: bank, transactions, summary, flags
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    # Read CSV
    with open(filepath, encoding="utf-8-sig") as f:
        # Try to detect delimiter (comma or semicolon)
        sample = f.read(1024)
        f.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.DictReader(f, delimiter=delimiter)
        headers = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        return {"error": f"No data found in {filepath.name}"}

    # Detect bank format
    bank_format = detect_bank_format(headers)
    if not bank_format:
        return {
            "error": f"Unrecognised CSV format in {filepath.name}",
            "headers_found": headers,
            "hint": "Check the supported banks list in the README."
        }

    print(f"  Detected: {bank_format.name} | {len(rows)} transactions")

    # Parse all rows
    transactions = []
    parse_errors = []

    for i, row in enumerate(rows):
        try:
            txn = bank_format.parse_row(row)
            txn["category"] = categorise_transaction(
                txn.get("description", ""), txn.get("reference", "")
            )
            transactions.append(txn)
        except Exception as e:
            parse_errors.append({"row": i + 1, "error": str(e), "data": dict(row)})

    # Calculate summary
    total_credits = sum(t["amount"] for t in transactions if t["amount"] > 0)
    total_debits = sum(t["amount"] for t in transactions if t["amount"] < 0)
    net_movement = total_credits + total_debits

    category_summary = {}
    for txn in transactions:
        cat = txn["category"]
        category_summary[cat] = category_summary.get(cat, 0) + abs(txn["amount"])

    # Flag uncategorised transactions for manual review
    flags = [t for t in transactions if t["category"] == "uncategorised"]

    return {
        "bank": bank_format.name,
        "file": filepath.name,
        "entity_id": entity_id,
        "transaction_count": len(transactions),
        "transactions": transactions,
        "summary": {
            "total_credits": total_credits,
            "total_debits": total_debits,
            "net_movement": net_movement,
            "category_breakdown": category_summary,
        },
        "flags": flags,
        "parse_errors": parse_errors,
    }


def display_results(result: dict, base_currency: str = "GBP"):
    """Display processing results in a readable format."""

    if "error" in result:
        print(f"\n  ERROR: {result['error']}")
        if "headers_found" in result:
            print(f"  Headers found: {result['headers_found']}")
        return

    summary = result["summary"]
    currency = result["transactions"][0]["currency"] if result["transactions"] else base_currency

    print(f"\n{'=' * 65}")
    print(f"  PROCESSING RESULTS: {result['file']}")
    print(f"  Bank: {result['bank']} | Transactions: {result['transaction_count']}")
    print(f"{'=' * 65}")

    # Financial summary
    summary_rows = [
        ["Total Credits (In)", format_currency(summary["total_credits"], currency)],
        ["Total Debits (Out)", format_currency(abs(summary["total_debits"]), currency)],
        ["Net Movement", format_currency(summary["net_movement"], currency)],
    ]
    print(tabulate(summary_rows, tablefmt="rounded_outline"))

    # Category breakdown
    if summary["category_breakdown"]:
        print(f"\n  CATEGORY BREAKDOWN")
        cat_rows = [
            [cat.replace("_", " ").title(), format_currency(amount, currency)]
            for cat, amount in sorted(
                summary["category_breakdown"].items(),
                key=lambda x: x[1],
                reverse=True
            )
        ]
        print(tabulate(cat_rows, headers=["Category", "Amount"], tablefmt="rounded_outline"))

    # Recent transactions sample
    print(f"\n  RECENT TRANSACTIONS (last 10)")
    recent = result["transactions"][-10:]
    txn_rows = [
        [
            t["date"].strftime("%d %b %Y"),
            (t["description"] or "")[:35],
            t["category"],
            format_currency(t["amount"], t["currency"]),
        ]
        for t in recent
    ]
    print(tabulate(
        txn_rows,
        headers=["Date", "Description", "Category", "Amount"],
        tablefmt="rounded_outline"
    ))

    # Flags
    if result["flags"]:
        print(f"\n  ⚠ {len(result['flags'])} TRANSACTIONS NEED MANUAL REVIEW (uncategorised)")
        flag_rows = [
            [
                f["date"].strftime("%d %b %Y"),
                (f["description"] or "")[:40],
                format_currency(f["amount"], f["currency"]),
            ]
            for f in result["flags"][:10]
        ]
        print(tabulate(
            flag_rows,
            headers=["Date", "Description", "Amount"],
            tablefmt="rounded_outline"
        ))

    if result["parse_errors"]:
        print(f"\n  ❌ {len(result['parse_errors'])} rows could not be parsed")

    print()


def export_for_review(result: dict, output_dir: str = "."):
    """Export processed transactions to a standardised CSV for review before SoftLedger import."""
    if "error" in result or not result.get("transactions"):
        return

    filename = f"processed_{result['bank'].lower()}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    filepath = Path(output_dir) / filename

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "description", "reference", "amount", "currency",
            "balance", "category", "bank"
        ])
        writer.writeheader()
        for txn in result["transactions"]:
            writer.writerow({
                "date": txn["date"].strftime("%Y-%m-%d"),
                "description": txn.get("description", ""),
                "reference": txn.get("reference", ""),
                "amount": txn.get("amount", 0),
                "currency": txn.get("currency", ""),
                "balance": txn.get("balance", 0),
                "category": txn.get("category", ""),
                "bank": txn.get("bank", ""),
            })

    print(f"  Exported for review: {filepath}")
    return str(filepath)


# ─── CLI ENTRY POINT ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bank CSV to SoftLedger Middleware",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported banks:
  Wise, HSBC, Barclays, Santander, OpenPayd, Privat3, Bison, Finductive

Examples:
  python scripts/bank_csv_processor.py --file wise_jan.csv
  python scripts/bank_csv_processor.py --file hsbc_feb.csv --export
  python scripts/bank_csv_processor.py --dir ./statements/ --export
        """
    )
    parser.add_argument("--file", help="Path to a single bank CSV file")
    parser.add_argument("--dir", help="Directory containing multiple bank CSV files")
    parser.add_argument("--entity", help="SoftLedger entity ID to post against")
    parser.add_argument("--currency", default="GBP", help="Base currency (default: GBP)")
    parser.add_argument("--export", action="store_true", help="Export processed CSV for review")
    parser.add_argument("--push", action="store_true", help="Push to SoftLedger after review (coming soon)")

    args = parser.parse_args()

    if not args.file and not args.dir:
        parser.print_help()
        sys.exit(1)

    print(f"\n{'=' * 65}")
    print(f"  BANK CSV TO SOFTLEDGER MIDDLEWARE")
    print(f"  {datetime.now().strftime('%d %B %Y at %H:%M')}")
    print(f"{'=' * 65}\n")

    files_to_process = []

    if args.file:
        files_to_process.append(args.file)

    if args.dir:
        directory = Path(args.dir)
        files_to_process.extend([
            str(f) for f in directory.glob("*.csv")
        ])

    if not files_to_process:
        print("No CSV files found to process.")
        sys.exit(1)

    print(f"Processing {len(files_to_process)} file(s)...\n")

    all_results = []
    for filepath in files_to_process:
        print(f"Processing: {Path(filepath).name}")
        result = process_csv(filepath, entity_id=args.entity, base_currency=args.currency)
        display_results(result, args.currency)
        all_results.append(result)

        if args.export and "transactions" in result:
            export_for_review(result)

    if args.push:
        print("\n  SoftLedger push coming in next release.")
        print("  Use --export to generate a review CSV first.")

    # Group summary if multiple files
    if len(all_results) > 1:
        successful = [r for r in all_results if "transactions" in r]
        total_txns = sum(r["transaction_count"] for r in successful)
        print(f"\n{'=' * 65}")
        print(f"  BATCH SUMMARY")
        print(f"  Files processed: {len(files_to_process)}")
        print(f"  Successful: {len(successful)}")
        print(f"  Total transactions: {total_txns}")
        print(f"{'=' * 65}\n")
