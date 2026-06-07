"""
FX Rate Fetcher
Retrieves live foreign exchange rates for multi-currency consolidation.
Uses the free Open Exchange Rates API (no key required for basic rates).
"""

import requests
from datetime import datetime


def get_live_rates(base_currency: str = "GBP") -> dict:
    """
    Fetch live FX rates from a free public API.
    Returns a dictionary of currency codes to rates relative to base currency.

    Args:
        base_currency: The base currency for conversion (default GBP)

    Returns:
        dict: {currency_code: rate} e.g. {"USD": 1.27, "EUR": 1.17, ...}
    """
    try:
        # Using the European Central Bank's free API as fallback
        response = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": base_currency},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            rates = data.get("rates", {})
            rates[base_currency] = 1.0  # Base currency always 1:1
            return rates
    except requests.RequestException:
        pass

    # Fallback to approximate rates if API unavailable
    print("Warning: Could not fetch live FX rates. Using approximate fallback rates.")
    return _fallback_rates(base_currency)


def convert_to_base(amount: float, currency: str, rates: dict, base: str = "GBP") -> float:
    """
    Convert an amount from a foreign currency to the base currency.

    Args:
        amount: The amount to convert
        currency: The source currency code (e.g. "USD")
        rates: Dictionary of exchange rates from get_live_rates()
        base: The target base currency (default GBP)

    Returns:
        float: The converted amount in the base currency
    """
    if currency == base:
        return amount

    rate = rates.get(currency)
    if not rate:
        print(f"Warning: No rate found for {currency}. Treating as 1:1 with {base}.")
        return amount

    # Rate is expressed as: 1 GBP = X foreign currency
    # So to convert foreign to GBP: amount / rate
    return amount / rate


def format_currency(amount: float, currency: str = "GBP") -> str:
    """Format a number as a currency string."""
    symbols = {"GBP": "£", "USD": "$", "EUR": "€", "AUD": "A$", "CAD": "C$"}
    symbol = symbols.get(currency, currency + " ")
    return f"{symbol}{amount:,.2f}"


def _fallback_rates(base: str) -> dict:
    """Approximate fallback rates when live API is unavailable."""
    gbp_rates = {
        "USD": 1.27, "EUR": 1.17, "AUD": 1.93, "CAD": 1.73,
        "NZD": 2.08, "HKD": 9.93, "SGD": 1.70, "CHF": 1.13,
        "JPY": 197.5, "MXN": 21.5, "BRL": 6.4,
    }

    if base == "GBP":
        rates = gbp_rates.copy()
        rates["GBP"] = 1.0
        return rates

    # If base is not GBP, convert through GBP
    base_rate = gbp_rates.get(base, 1.0)
    return {
        currency: rate / base_rate
        for currency, rate in gbp_rates.items()
    }
