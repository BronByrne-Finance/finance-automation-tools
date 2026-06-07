"""
SoftLedger Authentication Handler
Manages OAuth2 token acquisition and refresh for SoftLedger API.
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

SOFTLEDGER_AUTH_URL = "https://auth.softledger.com/oauth/token"
SOFTLEDGER_API_BASE = "https://api.softledger.com/v2"


class SoftLedgerAuth:
    """
    Handles authentication with the SoftLedger API using OAuth2 client credentials.

    Usage:
        auth = SoftLedgerAuth()
        token = auth.get_token()
        headers = auth.get_headers()
    """

    def __init__(self):
        self.client_id = os.getenv("SOFTLEDGER_CLIENT_ID")
        self.client_secret = os.getenv("SOFTLEDGER_CLIENT_SECRET")
        self.tenant_uuid = os.getenv("SOFTLEDGER_TENANT_UUID")
        self._token = None
        self._token_expiry = 0

        if not all([self.client_id, self.client_secret, self.tenant_uuid]):
            raise ValueError(
                "Missing SoftLedger credentials. "
                "Please set SOFTLEDGER_CLIENT_ID, SOFTLEDGER_CLIENT_SECRET "
                "and SOFTLEDGER_TENANT_UUID in your .env file."
            )

    def get_token(self) -> str:
        """
        Returns a valid access token, refreshing if expired.
        SoftLedger tokens typically expire after 24 hours.
        """
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        response = requests.post(
            SOFTLEDGER_AUTH_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "audience": f"https://api.softledger.com",
            },
            timeout=30,
        )

        if response.status_code != 200:
            raise ConnectionError(
                f"SoftLedger authentication failed: {response.status_code} {response.text}"
            )

        data = response.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 86400)

        return self._token

    def get_headers(self) -> dict:
        """Returns headers with valid Bearer token for API requests."""
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Content-Type": "application/json",
            "tenantUUID": self.tenant_uuid,
        }
