"""
SoftLedger API Client
Base client for making authenticated requests to the SoftLedger REST API.
"""

import requests
from auth.softledger_auth import SoftLedgerAuth, SOFTLEDGER_API_BASE


class SoftLedgerClient:
    """
    Base API client for SoftLedger. Handles authentication, pagination
    and error handling for all API requests.

    Usage:
        client = SoftLedgerClient()
        entities = client.get("/locations")
    """

    def __init__(self):
        self.auth = SoftLedgerAuth()
        self.base_url = SOFTLEDGER_API_BASE

    def get(self, endpoint: str, params: dict = None) -> dict:
        """Make a GET request to the SoftLedger API."""
        url = f"{self.base_url}{endpoint}"
        response = requests.get(
            url,
            headers=self.auth.get_headers(),
            params=params or {},
            timeout=30,
        )
        self._handle_errors(response)
        return response.json()

    def get_all(self, endpoint: str, params: dict = None) -> list:
        """
        Fetch all records from a paginated SoftLedger endpoint.
        SoftLedger uses limit/offset pagination with a default page size of 100.
        """
        all_records = []
        offset = 0
        limit = 100
        params = params or {}

        while True:
            params.update({"limit": limit, "offset": offset})
            response = self.get(endpoint, params)

            # SoftLedger returns data in a 'data' array
            records = response.get("data", [])
            all_records.extend(records)

            # Check if there are more pages
            total = response.get("totalItems", 0)
            offset += limit

            if offset >= total or not records:
                break

        return all_records

    def post(self, endpoint: str, payload: dict) -> dict:
        """Make a POST request to the SoftLedger API."""
        url = f"{self.base_url}{endpoint}"
        response = requests.post(
            url,
            headers=self.auth.get_headers(),
            json=payload,
            timeout=30,
        )
        self._handle_errors(response)
        return response.json()

    def _handle_errors(self, response: requests.Response):
        """Raise informative errors for failed API requests."""
        if response.status_code == 401:
            raise PermissionError("SoftLedger authentication failed. Check your API credentials.")
        elif response.status_code == 403:
            raise PermissionError("Insufficient permissions for this SoftLedger API endpoint.")
        elif response.status_code == 404:
            raise ValueError(f"SoftLedger endpoint not found: {response.url}")
        elif response.status_code >= 400:
            raise ConnectionError(
                f"SoftLedger API error {response.status_code}: {response.text}"
            )
