"""
pinterest_auth.py
─────────────────
Shared Pinterest OAuth token management module.

Imported by any script that calls the Pinterest API:
  - post_pin.py
  - score_products.py

Usage:
    from pinterest_auth import PinterestAuth

    auth  = PinterestAuth()
    token = auth.token           # current access token
    headers = auth.headers()     # ready-to-use Authorization headers

    # On 401 — refresh and retry:
    response = requests.get(url, headers=auth.headers())
    if response.status_code == 401:
        auth.refresh()
        response = requests.get(url, headers=auth.headers())
"""

import os
import base64
import requests


class PinterestAuth:
    """
    Manages Pinterest OAuth2 access tokens.

    Reads credentials from environment variables — never hardcoded.
    Refreshes the access token automatically when refresh() is called.
    Logs a warning if Pinterest issues a new refresh token so you know
    to update the GitHub Secret before the old one expires.
    """

    TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
    SCOPES    = "pins:write,pins:read,boards:read,boards:write,user_accounts:read"

    def __init__(self):
        self._token         = os.environ["PINTEREST_TOKEN"]
        self._refresh_token = os.environ["PINTEREST_REFRESH_TOKEN"]
        self._app_id        = os.environ["PINTEREST_APP_ID"]
        self._app_secret    = os.environ["PINTEREST_APP_SECRET"]

    @property
    def token(self):
        return self._token

    def headers(self):
        """Return Authorization headers ready to pass to requests."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type":  "application/json"
        }

    def refresh(self):
        """
        Exchange the refresh token for a new access token.
        Updates self._token in place.
        Warns if Pinterest issues a new refresh token (update GitHub Secret).
        """
        print("Refreshing Pinterest access token...")

        credentials = base64.b64encode(
            f"{self._app_id}:{self._app_secret}".encode()
        ).decode()

        resp = requests.post(
            self.TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type":  "application/x-www-form-urlencoded"
            },
            data={
                "grant_type":    "refresh_token",
                "refresh_token": self._refresh_token,
                "scope":         self.SCOPES
            }
        )

        if not resp.ok:
            print(f"Token refresh failed {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        data            = resp.json()
        self._token     = data["access_token"]

        # Pinterest may issue a new refresh token near its 1-year expiry
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            print("=" * 60)
            print("NEW REFRESH TOKEN ISSUED — update GitHub Secret:")
            print(f"PINTEREST_REFRESH_TOKEN = {new_refresh}")
            print("=" * 60)

        print("Access token refreshed successfully.")
        return self._token

    def get(self, url, **kwargs):
        """
        GET request with automatic token refresh on 401.
        Drop-in replacement for requests.get() for Pinterest API calls.
        """
        resp = requests.get(url, headers=self.headers(), **kwargs)
        if resp.status_code == 401:
            print(f"Got 401 on GET {url} — refreshing token...")
            self.refresh()
            resp = requests.get(url, headers=self.headers(), **kwargs)
        return resp

    def post(self, url, **kwargs):
        """
        POST request with automatic token refresh on 401.
        Drop-in replacement for requests.post() for Pinterest API calls.
        """
        resp = requests.post(url, headers=self.headers(), **kwargs)
        if resp.status_code == 401:
            print(f"Got 401 on POST {url} — refreshing token...")
            self.refresh()
            resp = requests.post(url, headers=self.headers(), **kwargs)
        return resp