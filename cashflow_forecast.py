"""
Rolling 13-Week Cash Flow Forecaster
======================================
Generates a rolling cash flow forecast using actual transaction data
from SoftLedger, with multi-currency FX conversion and scenario analysis.

Built for finance teams managing multi-entity, multi-currency operations
where manual cash flow forecasting in Excel becomes unmanageable at scale.

Usage:
    python scripts/cashflow_forecast.py
    python scripts/cashflow_forecast.py --weeks 26 --currency USD
    python scripts/cashflow_forecast.py --scenario stress
"""

import argparse
import sys
import os
from datetime import datetime, timedelta
from tabulate import tabulate

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.api_client import SoftLedgerClient
from utils.fx_rates import get_live_rates, convert_to_base, format_currency


# Stress scenario multipliers
STRESS_SCENARIOS = {
    "base": {"inflows": 1.0, "outflows": 1.0, "label": "Base Case"},
    "stress": {"inflows": 0.7, "outflows": 1.15, "label": "Stress Case (30% inflow reduction, 15% cost increase)"},
    "upside": {"inflows": 1.2, "outflows": 0.95, "label": "Upside Case (20% inflow increase, 5% cost reduction)"},
}

# Minimum runway threshold in weeks before alert triggers
RUNWAY_ALERT_WEEKS = 12


def fetch_recent_transactions(client: SoftLedgerClient, days: int = 90) -> list:
    """
    Fetch recent transactions to use as the basis for forecasting.
    Uses trailing 90-day actuals to project forward.
    """
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    date_to = datetime.now().strftime("%Y-%m-%d")

    try:
        transactions = client.get_all(
            "/transactions",
            params={"dateFrom": date_from, "dateTo": date_to}
        )
        return transactions
    except Exception as e:
        print(f"Warning: Could not fetch transactions: {e}")
        return []


def calculate_weekly_averages(transactions: list, fx_rates: dict, base_currency: str) -> dict:
    """
    Calculate average weekly inflows and outflows from historical transactions.
    Returns categorised cash flow averages in the base currency.
    """
    weekly_data = {
        "avg_inflows": 0.0,
        "avg_outflows": 0.0,
        "avg_payroll": 0.0,
        "avg_suppliers": 0.0,
        "avg_tax": 0.0,
        "avg_other": 0.0,
    }

    total_inflows = 0.0
    total_outflows = 0.0
    weeks_sampled = 13  # Use 13 weeks of history

    for txn in transactions:
        amount = float(txn.get("amount", 0) or 0)
        currency = txn.get("currency", base_currency)
        amount_base = convert_to_base(abs(amount), currency, fx_rates, base_currency)
        description = (txn.get("description", "") or "").lower()

        if amount > 0:
            total_inflows += amount_base
        else:
            total_outflows += amount_base

            # Categorise outflows by description keywords
            if any(term in description for term in ["payroll", "salary", "wages", "eor"]):
                weekly_data["avg_payroll"] += amount_base
            elif any(term in description for term in ["supplier", "vendor", "invoice"]):
                weekly_data["avg_suppliers"] += amount_base
            elif any(term in description for term in ["tax", "vat", "hmrc", "irs"]):
                weekly_data["avg_tax"] += amount_base
            else:
                weekly_data["avg_other"] += amount_base

    # Convert totals to weekly averages
    weekly_data["avg_inflows"] = total_inflows / weeks_sampled
    weekly_data["avg_outflows"] = total_outflows / weeks_sampled
    weekly_data["avg_payroll"] = weekly_data["avg_payroll"] / weeks_sampled
    weekly_data["avg_suppliers"] = weekly_data["avg_suppliers"] / weeks_sampled
    weekly_data["avg_tax"] = weekly_data["avg_tax"] / weeks_sampled
    weekly_data["avg_other"] = weekly_data["avg_other"] / weeks_sampled

    return weekly_data


def fetch_current_cash_balance(client: SoftLedgerClient, fx_rates: dict, base_currency: str) -> float:
    """Fetch the current consolidated cash balance across all entities."""
    try:
        locations = client.get_all("/locations")
        total_cash = 0.0

        for location in locations:
            location_id = location.get("id")
            currency = location.get("currency", base_currency)

            tb = client.get("/reports/trialBalance", params={"locationId": location_id})
            accounts = tb.get("data", [])

            for account in accounts:
                account_name = (account.get("name", "") or "").lower()
                account_type = (account.get("type", "") or "").upper()
                balance = float(account.get("balance", 0) or 0)

                if account_type == "ASSET" and any(
                    term in account_name for term in ["cash", "bank", "current account"]
                ):
                    total_cash += convert_to_base(balance, currency, fx_rates, base_currency)

        return total_cash

    except Exception as e:
        print(f"Warning: Could not fetch current cash balance: {e}")
        return 0.0


def generate_forecast(
    opening_balance: float,
    weekly_averages: dict,
    weeks: int,
    scenario: str,
    base_currency: str
) -> list:
    """
    Generate weekly cash flow forecast rows.
    Applies scenario multipliers to inflows and outflows.
    """
    scenario_params = STRESS_SCENARIOS.get(scenario, STRESS_SCENARIOS["base"])
    inflow_multiplier = scenario_params["inflows"]
    outflow_multiplier = scenario_params["outflows"]

    forecast_rows = []
    running_balance = opening_balance

    for week_num in range(1, weeks + 1):
        week_date = datetime.now() + timedelta(weeks=week_num)
        week_label = f"Wk {week_num} ({week_date.strftime('%d %b')})"

        projected_inflows = weekly_averages["avg_inflows"] * inflow_multiplier
        projected_payroll = weekly_averages["avg_payroll"] * outflow_multiplier
        projected_suppliers = weekly_averages["avg_suppliers"] * outflow_multiplier
        projected_tax = weekly_averages["avg_tax"] * outflow_multiplier
        projected_other = weekly_averages["avg_other"] * outflow_multiplier
        projected_outflows = projected_payroll + projected_suppliers + projected_tax + projected_other

        net_movement = projected_inflows - projected_outflows
        running_balance += net_movement

        # Flag low runway weeks
        flag = ""
        if running_balance < 0:
            flag = "NEGATIVE"
        elif week_num <= RUNWAY_ALERT_WEEKS and running_balance < (projected_outflows * 4):
            flag = "LOW"

        forecast_rows.append({
            "week": week_label,
            "inflows": projected_inflows,
            "outflows": projected_outflows,
            "payroll": projected_payroll,
            "suppliers": projected_suppliers,
            "tax": projected_tax,
            "net": net_movement,
            "balance": running_balance,
            "flag": flag,
        })

    return forecast_rows


def display_forecast(
    forecast_rows: list,
    opening_balance: float,
    scenario: str,
    base_currency: str
):
    """Display the forecast in a readable table format."""
    scenario_params = STRESS_SCENARIOS.get(scenario, STRESS_SCENARIOS["base"])

    print("\n" + "=" * 80)
    print(f"  ROLLING CASH FLOW FORECAST")
    print(f"  Scenario: {scenario_params['label']}")
    print(f"  Opening Balance: {format_currency(opening_balance, base_currency)}")
    print(f"  Generated: {datetime.now().strftime('%d %B %Y at %H:%M')}")
    print("=" * 80 + "\n")

    table_rows = []
    for row in forecast_rows:
        flag_display = "⚠ LOW" if row["flag"] == "LOW" else ("❌ NEG" if row["flag"] == "NEGATIVE" else "")
        table_rows.append([
            row["week"],
            format_currency(row["inflows"], base_currency),
            format_currency(row["outflows"], base_currency),
            format_currency(row["net"], base_currency),
            format_currency(row["balance"], base_currency),
            flag_display,
        ])

    headers = ["Week", "Inflows", "Outflows", "Net Movement", "Closing Balance", "Alert"]
    print(tabulate(table_rows, headers=headers, tablefmt="rounded_outline"))

    # Summary statistics
    min_balance = min(row["balance"] for row in forecast_rows)
    max_balance = max(row["balance"] for row in forecast_rows)
    final_balance = forecast_rows[-1]["balance"]
    negative_weeks = [row["week"] for row in forecast_rows if row["flag"] == "NEGATIVE"]
    low_weeks = [row["week"] for row in forecast_rows if row["flag"] == "LOW"]

    print(f"\n  FORECAST SUMMARY")
    print(f"  Opening Balance:  {format_currency(opening_balance, base_currency)}")
    print(f"  Closing Balance:  {format_currency(final_balance, base_currency)}")
    print(f"  Minimum Balance:  {format_currency(min_balance, base_currency)}")
    print(f"  Maximum Balance:  {format_currency(max_balance, base_currency)}")

    if negative_weeks:
        print(f"\n  ❌ NEGATIVE BALANCE WEEKS: {', '.join(negative_weeks)}")
    if low_weeks:
        print(f"  ⚠ LOW RUNWAY WEEKS: {', '.join(low_weeks)}")
    if not negative_weeks and not low_weeks:
        print(f"\n  ✓ No cash flow alerts in forecast period")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rolling Cash Flow Forecaster")
    parser.add_argument("--weeks", type=int, default=13, help="Forecast weeks (default: 13)")
    parser.add_argument("--currency", default="GBP", help="Base currency (default: GBP)")
    parser.add_argument(
        "--scenario",
        choices=["base", "stress", "upside"],
        default="base",
        help="Forecast scenario (default: base)"
    )
    args = parser.parse_args()

    print(f"\nInitialising SoftLedger Cash Flow Forecaster...")
    client = SoftLedgerClient()

    print("Fetching live FX rates...")
    fx_rates = get_live_rates(args.currency)

    print("Fetching current cash position...")
    opening_balance = fetch_current_cash_balance(client, fx_rates, args.currency)
    print(f"Current cash position: {format_currency(opening_balance, args.currency)}")

    print("Analysing historical transaction patterns...")
    transactions = fetch_recent_transactions(client)
    weekly_averages = calculate_weekly_averages(transactions, fx_rates, args.currency)

    print(f"Generating {args.weeks}-week forecast ({args.scenario} scenario)...\n")
    forecast_rows = generate_forecast(
        opening_balance, weekly_averages, args.weeks, args.scenario, args.currency
    )

    display_forecast(forecast_rows, opening_balance, args.scenario, args.currency)
