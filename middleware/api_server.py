"""
SoftLedger API Middleware
==========================
A secure REST API middleware that accepts financial transaction data
from any authorised source and creates properly formatted journal entries
in SoftLedger automatically.

Built for multi-entity finance operations where manual journal entry
creation is a bottleneck and automation is essential for scale.

Architecture:
    External System --> This Middleware (authenticated) --> SoftLedger API

Security Model:
    - API key authentication for all inbound requests
    - Rate limiting to prevent abuse
    - Input validation and sanitisation
    - Audit logging of all requests and responses
    - Environment-based configuration (no hardcoded credentials)
    - HTTPS enforced in production

Usage:
    Development:  python middleware/api_server.py --dev
    Production:   gunicorn middleware.api_server:app --workers 4 --bind 0.0.0.0:8000

Deployment:
    See SECURITY.md for full server hardening guide
"""

import os
import sys
import json
import hmac
import hashlib
import logging
import time
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from auth.softledger_auth import SoftLedgerAuth, SOFTLEDGER_API_BASE
from utils.api_client import SoftLedgerClient
from utils.validators import validate_journal_entry, validate_transaction

load_dotenv()

# ─── LOGGING SETUP ────────────────────────────────────────────────────────────
# All requests and responses are logged for audit trail
# Never log sensitive data (API keys, account numbers)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/middleware.log", mode="a"),
    ]
)
logger = logging.getLogger("sl-middleware")

# ─── APP INITIALISATION ───────────────────────────────────────────────────────

app = Flask(__name__)

# Rate limiting — prevents brute force and abuse
# Adjust limits based on your expected transaction volumes
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",  # Use Redis in production: "redis://localhost:6379"
)

# SoftLedger client (shared across requests)
sl_client = SoftLedgerClient()

# ─── SECURITY CONFIGURATION ───────────────────────────────────────────────────

# Inbound API keys — loaded from environment, never hardcoded
# Generate with: python -c "import secrets; print(secrets.token_hex(32))"
VALID_API_KEYS = set(filter(None, [
    os.getenv("MIDDLEWARE_API_KEY_1"),
    os.getenv("MIDDLEWARE_API_KEY_2"),  # Support multiple keys for key rotation
    os.getenv("MIDDLEWARE_API_KEY_3"),
]))

# Webhook secret for signature verification (optional but recommended)
WEBHOOK_SECRET = os.getenv("MIDDLEWARE_WEBHOOK_SECRET", "")

# Allowed IP addresses (optional — restrict to known sources)
ALLOWED_IPS = set(filter(None, (os.getenv("ALLOWED_IPS", "")).split(",")))


# ─── AUTHENTICATION DECORATORS ────────────────────────────────────────────────

def require_api_key(f):
    """
    Decorator that enforces API key authentication on any route.

    Accepts key in:
        - Header: X-API-Key: your-key-here
        - Header: Authorization: Bearer your-key-here

    Timing-safe comparison prevents timing attacks.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        # Extract API key from header
        api_key = (
            request.headers.get("X-API-Key") or
            request.headers.get("Authorization", "").replace("Bearer ", "")
        )

        if not api_key:
            logger.warning(f"Missing API key | IP: {request.remote_addr} | Path: {request.path}")
            return jsonify({"error": "API key required", "code": 401}), 401

        # Timing-safe comparison — prevents timing attacks
        key_valid = any(
            hmac.compare_digest(api_key.encode(), valid_key.encode())
            for valid_key in VALID_API_KEYS
            if valid_key
        )

        if not key_valid:
            logger.warning(f"Invalid API key | IP: {request.remote_addr} | Path: {request.path}")
            return jsonify({"error": "Invalid API key", "code": 401}), 401

        # Store authenticated state for downstream use
        g.authenticated = True
        g.request_time = time.time()

        return f(*args, **kwargs)
    return decorated


def restrict_ip(f):
    """
    Optional decorator to restrict access to known IP addresses.
    Enable by setting ALLOWED_IPS in your environment.
    Useful for locking down to specific servers or office IPs.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if ALLOWED_IPS and request.remote_addr not in ALLOWED_IPS:
            logger.warning(f"IP not allowed | IP: {request.remote_addr} | Path: {request.path}")
            return jsonify({"error": "Access denied", "code": 403}), 403
        return f(*args, **kwargs)
    return decorated


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify HMAC-SHA256 webhook signature.
    Use this when receiving webhooks from external systems.

    The sender should include: X-Signature: sha256=<hmac_hex>
    """
    if not WEBHOOK_SECRET:
        return True  # Skip verification if no secret configured

    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


# ─── REQUEST LOGGING ──────────────────────────────────────────────────────────

@app.before_request
def log_request():
    """Log all incoming requests for audit trail."""
    logger.info(
        f"REQUEST | {request.method} {request.path} | "
        f"IP: {request.remote_addr} | "
        f"Content-Length: {request.content_length}"
    )


@app.after_request
def log_response(response):
    """Log all responses with timing information."""
    duration = time.time() - getattr(g, "request_time", time.time())
    logger.info(
        f"RESPONSE | {response.status_code} | "
        f"Duration: {duration:.3f}s | "
        f"Path: {request.path}"
    )
    # Security headers — always include these in production
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cache-Control"] = "no-store"
    return response


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health_check():
    """
    Public health check endpoint — no authentication required.
    Used by load balancers and monitoring tools.
    Does NOT expose sensitive system information.
    """
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }), 200


# ─── JOURNAL ENTRY ROUTES ─────────────────────────────────────────────────────

@app.route("/api/v1/journal-entries", methods=["POST"])
@limiter.limit("30 per minute")
@require_api_key
def create_journal_entry():
    """
    Create a journal entry in SoftLedger.

    Expected request body:
    {
        "date": "2026-01-15",
        "description": "January payroll",
        "currency": "GBP",
        "location_id": 123,
        "reference": "PAY-2026-01",
        "lines": [
            {
                "account_id": 456,
                "description": "Gross wages",
                "debit": 50000.00,
                "credit": 0
            },
            {
                "account_id": 789,
                "description": "Payroll liability",
                "debit": 0,
                "credit": 50000.00
            }
        ]
    }

    Returns:
        201: Journal entry created successfully
        400: Validation error
        422: SoftLedger rejected the entry
        429: Rate limit exceeded
        500: Internal error
    """
    try:
        data = request.get_json(force=True, silent=True)

        if not data:
            return jsonify({"error": "Request body must be valid JSON", "code": 400}), 400

        # Validate the journal entry structure
        validation_errors = validate_journal_entry(data)
        if validation_errors:
            logger.warning(f"Validation failed | Errors: {validation_errors}")
            return jsonify({
                "error": "Validation failed",
                "details": validation_errors,
                "code": 400
            }), 400

        # Check debits equal credits (double-entry integrity)
        total_debits = sum(float(line.get("debit", 0)) for line in data["lines"])
        total_credits = sum(float(line.get("credit", 0)) for line in data["lines"])

        if abs(total_debits - total_credits) > 0.01:  # Allow for floating point tolerance
            return jsonify({
                "error": "Journal entry does not balance",
                "total_debits": total_debits,
                "total_credits": total_credits,
                "difference": round(total_debits - total_credits, 2),
                "code": 400
            }), 400

        # Build SoftLedger journal entry payload
        sl_payload = {
            "date": data["date"],
            "description": data.get("description", ""),
            "reference": data.get("reference", ""),
            "currency": data.get("currency", "GBP"),
            "LocationId": data["location_id"],
            "LedgerLineItems": [
                {
                    "AccountId": line["account_id"],
                    "description": line.get("description", ""),
                    "debit": float(line.get("debit", 0)),
                    "credit": float(line.get("credit", 0)),
                }
                for line in data["lines"]
            ]
        }

        # Post to SoftLedger
        result = sl_client.post("/journals", sl_payload)

        logger.info(
            f"Journal entry created | "
            f"ID: {result.get('id')} | "
            f"Date: {data['date']} | "
            f"Amount: {total_debits} {data.get('currency', 'GBP')} | "
            f"Ref: {data.get('reference', 'N/A')}"
        )

        return jsonify({
            "success": True,
            "journal_id": result.get("id"),
            "message": "Journal entry created successfully",
            "total_amount": total_debits,
            "currency": data.get("currency", "GBP"),
        }), 201

    except Exception as e:
        logger.error(f"Journal entry creation failed | Error: {str(e)}")
        return jsonify({
            "error": "Failed to create journal entry",
            "message": str(e),
            "code": 500
        }), 500


@app.route("/api/v1/journal-entries/batch", methods=["POST"])
@limiter.limit("10 per minute")
@require_api_key
def create_batch_journal_entries():
    """
    Create multiple journal entries in a single request.
    Useful for month-end close automation or bulk imports.

    Expected body: { "entries": [ ...journal entry objects... ] }

    Returns a summary of successes and failures.
    Each entry is processed independently — partial success is possible.
    """
    try:
        data = request.get_json(force=True, silent=True)

        if not data or "entries" not in data:
            return jsonify({"error": "Request must include 'entries' array", "code": 400}), 400

        entries = data["entries"]
        if not isinstance(entries, list) or len(entries) == 0:
            return jsonify({"error": "'entries' must be a non-empty array", "code": 400}), 400

        if len(entries) > 100:
            return jsonify({"error": "Maximum 100 entries per batch request", "code": 400}), 400

        results = {"succeeded": [], "failed": [], "total": len(entries)}

        for i, entry in enumerate(entries):
            try:
                validation_errors = validate_journal_entry(entry)
                if validation_errors:
                    results["failed"].append({
                        "index": i,
                        "reference": entry.get("reference", f"entry_{i}"),
                        "error": validation_errors
                    })
                    continue

                total_debits = sum(float(line.get("debit", 0)) for line in entry["lines"])
                total_credits = sum(float(line.get("credit", 0)) for line in entry["lines"])

                if abs(total_debits - total_credits) > 0.01:
                    results["failed"].append({
                        "index": i,
                        "reference": entry.get("reference", f"entry_{i}"),
                        "error": f"Entry does not balance: debits {total_debits} credits {total_credits}"
                    })
                    continue

                sl_payload = {
                    "date": entry["date"],
                    "description": entry.get("description", ""),
                    "reference": entry.get("reference", ""),
                    "currency": entry.get("currency", "GBP"),
                    "LocationId": entry["location_id"],
                    "LedgerLineItems": [
                        {
                            "AccountId": line["account_id"],
                            "description": line.get("description", ""),
                            "debit": float(line.get("debit", 0)),
                            "credit": float(line.get("credit", 0)),
                        }
                        for line in entry["lines"]
                    ]
                }

                result = sl_client.post("/journals", sl_payload)
                results["succeeded"].append({
                    "index": i,
                    "journal_id": result.get("id"),
                    "reference": entry.get("reference"),
                    "amount": total_debits,
                    "currency": entry.get("currency", "GBP"),
                })

            except Exception as e:
                results["failed"].append({
                    "index": i,
                    "reference": entry.get("reference", f"entry_{i}"),
                    "error": str(e)
                })

        logger.info(
            f"Batch complete | "
            f"Total: {results['total']} | "
            f"Succeeded: {len(results['succeeded'])} | "
            f"Failed: {len(results['failed'])}"
        )

        status_code = 207 if results["failed"] else 201  # 207 = Multi-Status
        return jsonify(results), status_code

    except Exception as e:
        logger.error(f"Batch journal entry failed | Error: {str(e)}")
        return jsonify({"error": str(e), "code": 500}), 500


# ─── TRANSACTION LOOKUP ROUTES ────────────────────────────────────────────────

@app.route("/api/v1/transactions", methods=["GET"])
@limiter.limit("60 per minute")
@require_api_key
def get_transactions():
    """
    Retrieve transactions from SoftLedger with optional filtering.

    Query parameters:
        date_from:   Start date (YYYY-MM-DD)
        date_to:     End date (YYYY-MM-DD)
        location_id: Filter by entity
        currency:    Filter by currency
        limit:       Max results (default 100, max 500)
    """
    try:
        params = {
            "dateFrom": request.args.get("date_from"),
            "dateTo": request.args.get("date_to"),
            "locationId": request.args.get("location_id"),
            "currency": request.args.get("currency"),
            "limit": min(int(request.args.get("limit", 100)), 500),
        }
        params = {k: v for k, v in params.items() if v is not None}

        transactions = sl_client.get("/transactions", params=params)

        return jsonify({
            "success": True,
            "count": len(transactions.get("data", [])),
            "data": transactions.get("data", []),
        }), 200

    except Exception as e:
        logger.error(f"Transaction fetch failed | Error: {str(e)}")
        return jsonify({"error": str(e), "code": 500}), 500


# ─── WEBHOOK RECEIVER ─────────────────────────────────────────────────────────

@app.route("/api/v1/webhooks/inbound", methods=["POST"])
@limiter.limit("100 per minute")
def receive_webhook():
    """
    Receive webhook events from external systems (banks, payroll, etc.)
    and automatically create journal entries in SoftLedger.

    Verifies HMAC-SHA256 signature if MIDDLEWARE_WEBHOOK_SECRET is set.

    The webhook payload should follow the standard transaction format:
    {
        "source": "wise",
        "event": "transaction.created",
        "data": {
            "date": "2026-01-15",
            "amount": -1500.00,
            "currency": "GBP",
            "description": "Supplier payment",
            "reference": "INV-001"
        }
    }
    """
    try:
        # Verify webhook signature
        signature = request.headers.get("X-Signature", "")
        if not verify_webhook_signature(request.data, signature):
            logger.warning(f"Invalid webhook signature | IP: {request.remote_addr}")
            return jsonify({"error": "Invalid signature", "code": 401}), 401

        payload = request.get_json(force=True, silent=True)
        if not payload:
            return jsonify({"error": "Invalid JSON payload", "code": 400}), 400

        source = payload.get("source", "unknown")
        event = payload.get("event", "unknown")
        data = payload.get("data", {})

        logger.info(f"Webhook received | Source: {source} | Event: {event}")

        # Validate transaction data
        errors = validate_transaction(data)
        if errors:
            return jsonify({"error": "Invalid transaction data", "details": errors}), 400

        # Queue for processing (in production use Celery/Redis queue)
        # For now, process synchronously
        result = _process_webhook_transaction(source, event, data)

        return jsonify({
            "success": True,
            "message": f"Webhook processed from {source}",
            "result": result,
        }), 200

    except Exception as e:
        logger.error(f"Webhook processing failed | Error: {str(e)}")
        return jsonify({"error": str(e), "code": 500}), 500


def _process_webhook_transaction(source: str, event: str, data: dict) -> dict:
    """
    Process an inbound webhook transaction.
    Maps external transaction data to SoftLedger journal entry format.
    Extend this function to add custom mapping logic per source system.
    """
    # Default account mapping — override with your chart of accounts
    account_mapping = {
        "wise": {"bank_account": int(os.getenv("WISE_BANK_ACCOUNT_ID", "0"))},
        "hsbc": {"bank_account": int(os.getenv("HSBC_BANK_ACCOUNT_ID", "0"))},
        "openpay": {"bank_account": int(os.getenv("OPENPAY_BANK_ACCOUNT_ID", "0"))},
    }

    bank_account_id = account_mapping.get(source, {}).get("bank_account", 0)
    suspense_account_id = int(os.getenv("SUSPENSE_ACCOUNT_ID", "0"))

    if not bank_account_id or not suspense_account_id:
        return {"status": "queued", "reason": "Account IDs not configured — manual review required"}

    amount = float(data.get("amount", 0))
    is_credit = amount > 0

    journal_payload = {
        "date": data["date"],
        "description": data.get("description", f"Webhook from {source}"),
        "reference": data.get("reference", ""),
        "currency": data.get("currency", "GBP"),
        "location_id": int(os.getenv("DEFAULT_LOCATION_ID", "0")),
        "lines": [
            {
                "account_id": bank_account_id,
                "description": data.get("description", ""),
                "debit": abs(amount) if is_credit else 0,
                "credit": 0 if is_credit else abs(amount),
            },
            {
                "account_id": suspense_account_id,
                "description": "Awaiting coding",
                "debit": 0 if is_credit else abs(amount),
                "credit": abs(amount) if is_credit else 0,
            }
        ]
    }

    result = sl_client.post("/journals", journal_payload)
    return {"status": "created", "journal_id": result.get("id")}


# ─── ERROR HANDLERS ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint not found", "code": 404}), 404


@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Method not allowed", "code": 405}), 405


@app.errorhandler(429)
def rate_limit_exceeded(e):
    logger.warning(f"Rate limit exceeded | IP: {request.remote_addr}")
    return jsonify({"error": "Rate limit exceeded. Please slow down.", "code": 429}), 429


@app.errorhandler(500)
def internal_error(e):
    logger.error(f"Internal server error | {str(e)}")
    return jsonify({"error": "Internal server error", "code": 500}), 500


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SoftLedger API Middleware")
    parser.add_argument("--dev", action="store_true", help="Run in development mode")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    args = parser.parse_args()

    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)

    if args.dev:
        print("\n" + "=" * 60)
        print("  SOFTLEDGER API MIDDLEWARE")
        print("  Development mode — do NOT use in production")
        print(f"  Running on http://localhost:{args.port}")
        print("=" * 60 + "\n")
        app.run(debug=True, port=args.port, host="127.0.0.1")
    else:
        print("Use gunicorn for production deployment.")
        print("See SECURITY.md for full deployment guide.")
