"""
Multi-Entity Consolidated Dashboard
====================================
Pulls real-time financial data across all entities in a SoftLedger group
and displays a consolidated snapshot of the group's financial position.

This script addresses a common pain point in multi-entity finance:
getting a single consolidated view across multiple entities and currencies
without manual data gathering.

Usage:
    python scripts/entity_dashboard.py
    python scripts/entity_dashboard.py --currency USD
    python scripts/entity_dashboard.py --export csv
"""

import argparse
import csv
import sys
import os
from datetime import datetime
from tabulate import tabulate

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.api_client import SoftLedgerClient
from utils.fx_rates import get_live_rates, convert_to_base, format_currency


def fetch_entities(client: SoftLedgerClient) -> list:
    """Fetch all entities (locations) from SoftLedger."""
    print("Fetching entities...")
    locations = client.get_all("/locations")
    return locations


def fetch_trial_balance(client: SoftLedgerClient, location_id: int) -> dict:
    """
    Fetch the trial balance for a specific entity.
    Returns summarised P&L and balance sheet positions.
    """
    try:
        tb = client.get(f"/reports/trialBalance", params={"locationId": location_id})
        return tb
    except Exception as e:
        print(f"  Warning: Could not fetch trial balance for entity {location_id}: {e}")
        return {}


def summarise_trial_balance(tb_data: dict) -> dict:
    """
    Summarise trial balance data into key financial metrics.
    Maps SoftLedger account types to standard financial categories.
    """
    summary = {
        "total_assets": 0.0,
        "total_liabilities": 0.0,
        "total_equity": 0.0,
        "total_revenue": 0.0,
        "total_expenses": 0.0,
        "net_income": 0.0,
        "cash_position": 0.0,
    }

    accounts = tb_data.get("data", [])

    for account in accounts:
        account_type = account.get("type", "").upper()
        balance = float(account.get("balance", 0) or 0)
        account_name = account.get("name", "").lower()

        if account_type == "ASSET":
            summary["total_assets"] += balance
            # Identify cash accounts by common naming conventions
            if any(term in account_name for term in ["cash", "bank", "current account"]):
                summary["cash_position"] += balance

        elif account_type == "LIABILITY":
            summary["total_liabilities"] += abs(balance)

        elif account_type == "EQUITY":
            summary["total_equity"] += abs(balance)

        elif account_type == "REVENUE" or account_type == "INCOME":
            summary["total_revenue"] += abs(balance)

        elif account_type == "EXPENSE":
            summary["total_expenses"] += abs(balance)

    summary["net_income"] = summary["total_revenue"] - summary["total_expenses"]

    return summary


def build_dashboard(base_currency: str = "GBP") -> None:
    """
    Main dashboard function. Fetches and displays consolidated group financials.
    """
    print("\n" + "=" * 70)
    print(f"  SOFTLEDGER MULTI-ENTITY CONSOLIDATED DASHBOARD")
    print(f"  Generated: {datetime.now().strftime('%d %B %Y at %H:%M')}")
    print(f"  Base Currency: {base_currency}")
    print("=" * 70 + "\n")

    client = SoftLedgerClient()

    # Fetch live FX rates
    print("Fetching live FX rates...")
    fx_rates = get_live_rates(base_currency)
    print(f"Base currency: {base_currency} | Rates fetched successfully\n")

    # Fetch all entities
    entities = fetch_entities(client)

    if not entities:
        print("No entities found. Check your SoftLedger configuration.")
        return

    print(f"Found {len(entities)} entities. Fetching financial data...\n")

    # Collect entity data
    entity_rows = []
    group_totals = {
        "total_assets": 0.0,
        "total_liabilities": 0.0,
        "total_revenue": 0.0,
        "total_expenses": 0.0,
        "net_income": 0.0,
        "cash_position": 0.0,
    }

    for entity in entities:
        entity_id = entity.get("id")
        entity_name = entity.get("name", "Unknown")
        entity_currency = entity.get("currency", base_currency)

        print(f"  Processing: {entity_name} ({entity_currency})...")

        tb = fetch_trial_balance(client, entity_id)
        summary = summarise_trial_balance(tb)

        # Convert to base currency
        converted = {
            key: convert_to_base(value, entity_currency, fx_rates, base_currency)
            for key, value in summary.items()
        }

        # Add to group totals
        for key in group_totals:
            group_totals[key] += converted.get(key, 0)

        entity_rows.append([
            entity_name,
            entity_currency,
            format_currency(summary["cash_position"], entity_currency),
            format_currency(summary["total_assets"], entity_currency),
            format_currency(summary["total_revenue"], entity_currency),
            format_currency(summary["net_income"], entity_currency),
            format_currency(converted["total_assets"], base_currency),
        ])

    # Display entity table
    print("\n" + "=" * 70)
    print("  ENTITY SUMMARY")
    print("=" * 70)
    headers = [
        "Entity", "CCY", "Cash Position",
        "Total Assets", "Revenue (YTD)", "Net Income",
        f"Assets ({base_currency})"
    ]
    print(tabulate(entity_rows, headers=headers, tablefmt="rounded_outline"))

    # Display group consolidated totals
    print("\n" + "=" * 70)
    print(f"  GROUP CONSOLIDATED POSITION ({base_currency})")
    print("=" * 70)

    consolidated_rows = [
        ["Total Group Assets", format_currency(group_totals["total_assets"], base_currency)],
        ["Total Group Liabilities", format_currency(group_totals["total_liabilities"], base_currency)],
        ["Total Group Revenue (YTD)", format_currency(group_totals["total_revenue"], base_currency)],
        ["Total Group Expenses (YTD)", format_currency(group_totals["total_expenses"], base_currency)],
        ["Group Net Income (YTD)", format_currency(group_totals["net_income"], base_currency)],
        ["Group Cash Position", format_currency(group_totals["cash_position"], base_currency)],
    ]

    print(tabulate(consolidated_rows, tablefmt="rounded_outline"))

    # Net assets
    net_assets = group_totals["total_assets"] - group_totals["total_liabilities"]
    print(f"\n  Net Group Assets: {format_currency(net_assets, base_currency)}")
    print(f"  Entities Consolidated: {len(entities)}")
    print(f"  Report generated: {datetime.now().strftime('%d %B %Y at %H:%M:%S')}\n")

    return entity_rows, group_totals


def export_to_csv(entity_rows: list, group_totals: dict, base_currency: str, filename: str = None):
    """Export dashboard data to CSV."""
    if not filename:
        filename = f"group_dashboard_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["MULTI-ENTITY CONSOLIDATED DASHBOARD"])
        writer.writerow([f"Generated: {datetime.now().strftime('%d %B %Y at %H:%M')}"])
        writer.writerow([f"Base Currency: {base_currency}"])
        writer.writerow([])
        writer.writerow(["Entity", "Currency", "Cash Position", "Total Assets",
                         "Revenue YTD", "Net Income", f"Assets {base_currency}"])
        writer.writerows(entity_rows)
        writer.writerow([])
        writer.writerow(["GROUP CONSOLIDATED TOTALS"])
        for key, value in group_totals.items():
            writer.writerow([key.replace("_", " ").title(), f"{value:,.2f}"])

    print(f"Dashboard exported to: {filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SoftLedger Multi-Entity Consolidated Dashboard"
    )
    parser.add_argument(
        "--currency",
        default="GBP",
        help="Base currency for consolidation (default: GBP)"
    )
    parser.add_argument(
        "--export",
        choices=["csv"],
        help="Export format (optional)"
    )
    args = parser.parse_args()

    result = build_dashboard(base_currency=args.currency)

    if result and args.export == "csv":
        entity_rows, group_totals = result
        export_to_csv(entity_rows, group_totals, args.currency)
