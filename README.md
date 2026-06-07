# finance-automation-tools
Bank CSV middleware for automating transaction import into SoftLedger — supporting specific bank
A Python toolkit for automating financial operations, reporting and analysis — built for use with the SoftLedger API and a range of banking providers.

Built by a Chartered Accountant with a BSc in Informatics — bridging the gap between accounting rigour and technical automation.

> **Disclaimer:** This is an independent open-source project and is not affiliated with, endorsed by or officially supported by SoftLedger. SoftLedger is a trademark of its respective owners. This toolkit uses SoftLedger's publicly documented REST API.

---

## What This Does

This toolkit provides practical Python scripts for finance teams using SoftLedger, covering:

- **Multi-entity consolidated reporting** — pull balances across all entities in real time
- **Multi-currency cash flow forecasting** — rolling 13-week forecast with FX conversion
- **Crypto transaction reconciliation** — reconcile on-chain transactions against journal entries
- **FX exposure calculator** — calculate group currency exposure across entities
- **Month-end close automation** — automate repetitive close tasks and reporting

---

## Why This Exists

Most accounting systems are built for accountants or engineers — rarely both. SoftLedger's API-first architecture makes it uniquely suited to automation, but finance teams often lack the technical tools to take advantage of it.

These scripts are written by someone who has implemented SoftLedger in a live multi-entity international environment, understands the accounting requirements and has built the automation to support them.

---

## Getting Started

### Prerequisites

```bash
pip install requests pandas python-dotenv tabulate matplotlib
```

### Authentication

SoftLedger uses API Key authentication. To get your API credentials:

1. Log into SoftLedger
2. Go to Settings > API Keys
3. Generate a new key with appropriate permissions
4. Create a `.env` file in the project root:

```
SOFTLEDGER_CLIENT_ID=your_client_id
SOFTLEDGER_CLIENT_SECRET=your_client_secret
SOFTLEDGER_TENANT_UUID=your_tenant_uuid
```

**Never commit your `.env` file to GitHub.** It is included in `.gitignore` by default.

---

## Scripts

### 1. Multi-Entity Dashboard (`entity_dashboard.py`)
Pulls consolidated balances across all entities and displays a real-time snapshot of group financial position.

### 2. Cash Flow Forecaster (`cashflow_forecast.py`)
Generates a rolling 13-week cash flow forecast from SoftLedger transaction data with multi-currency FX conversion.

### 3. Crypto Reconciliation (`crypto_reconcile.py`)
Reconciles crypto wallet transactions against SoftLedger journal entries, flagging unmatched items for review.

### 4. FX Exposure Calculator (`fx_exposure.py`)
Calculates group FX exposure across all entities and currencies, with optional hedging analysis.

---

## Project Structure

```
softledger-finance-tools/
├── README.md
├── .gitignore
├── requirements.txt
├── config/
│   └── settings.py          # Configuration and constants
├── auth/
│   └── softledger_auth.py   # Authentication handler
├── middleware
│   └── softledger_auth.py   # Authentication handler
├── scripts/
│   ├── entity_dashboard.py  # Multi-entity consolidated view
│   ├── cashflow_forecast.py # Rolling cash flow forecast
│   ├── crypto_reconcile.py  # Crypto reconciliation
│   └── fx_exposure.py       # FX exposure calculator
└── utils/
    ├── api_client.py        # Base API client
    ├── formatters.py        # Output formatting helpers
    └── fx_rates.py          # Live FX rate fetcher
```

---

## Background

This toolkit was developed out of practical necessity while managing finance across a 12-entity international FinTech group using SoftLedger as the core accounting platform.

The problems these scripts solve are real ones: manually pulling consolidated balances across entities is time-consuming, multi-currency cash flow forecasting in Excel breaks at scale, and crypto transaction reconciliation has no native tooling in most accounting systems.

---

## Contributions

This is an open project. If you work in finance and can code — or code and want to understand finance better — contributions are welcome.

---

## Author

**Bronwyn Byrne **
Chartered Accountant | BSc Informatics | Head of Finance
[LinkedIn](https://linkedin.com/in/bronbyrne)
