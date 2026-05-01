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
from config import supabase_get, supabase_patch, log


class PinterestAuth:
    """
    Manages Pinterest OAuth2 access tokens with database persistence.

    - Loads current tokens from Supabase 'settings' table.
    - Falls back to Environment Variables if DB is empty.
    - Automatically updates the DB when a token is refreshed.
    """

    TOKEN_URL = "https://api.pinterest.com/v5/oauth/token"
    SCOPES    = "pins:write,pins:read,boards:read,boards:write,user_accounts:read"

    def __init__(self):
        # Credentials for the refresh handshake (always from Env)
        self._app_id     = os.environ["PINTEREST_APP_ID"]
        self._app_secret = os.environ["PINTEREST_APP_SECRET"]

        # Load tokens from Supabase settings
        try:
            settings = supabase_get("settings", {"id": "eq.1"})
            if settings and len(settings) > 0:
                s = settings[0]
                self._token         = s.get("pinterest_access_token")
                self._refresh_token = s.get("pinterest_refresh_token")
                if not self._token:
                    # Fallback to Env if DB row exists but tokens are null
                    self._token = os.environ.get("PINTEREST_TOKEN")
                    self._refresh_token = os.environ.get("PINTEREST_REFRESH_TOKEN")
            else:
                # Fallback to Env
                self._token         = os.environ.get("PINTEREST_TOKEN")
                self._refresh_token = os.environ.get("PINTEREST_REFRESH_TOKEN")
        except Exception as e:
            log.warning(f"Could not load tokens from DB: {e}. Using Env fallback.")
            self._token         = os.environ.get("PINTEREST_TOKEN")
            self._refresh_token = os.environ.get("PINTEREST_REFRESH_TOKEN")

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
        Updates self._token and PERSISTS the new values to Supabase.
        """
        log.info("Refreshing Pinterest access token...")

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
            log.error(f"Token refresh failed {resp.status_code}: {resp.text}")
            resp.raise_for_status()

        data            = resp.json()
        self._token     = data["access_token"]
        
        # Pinterest may issue a new refresh token
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            log.info("New refresh token received.")

        # Persist to Supabase
        try:
            supabase_patch("settings?id=eq.1", {
                "pinterest_access_token":  self._token,
                "pinterest_refresh_token": self._refresh_token,
                "pinterest_updated_at":    "now()"
            })
            log.info("Saved refreshed tokens to Supabase settings table.")
        except Exception as e:
            log.error(f"Failed to persist refreshed tokens to DB: {e}")

        log.info("Access token refreshed successfully.")
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