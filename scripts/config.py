"""
config.py
─────────
Shared configuration for all pipeline scripts.

Provides:
- Supabase connection (URL, headers, helper functions)
- Amazon commission rates by category
- User-Agent rotation for scraping
- Logging setup
"""

import os
import random
import logging
import requests

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("pipeline")

# ── Supabase ────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

# ── Amazon commission rates (UK) ────────────────────────────
# Source: affiliate-program.amazon.co.uk/help/node/topic/GRXPHT8U84RAYDXZ
COMMISSION_RATES = {
    "fashion":        8.0,
    "luxury_beauty": 10.0,
    "beauty":         6.0,
    "home_kitchen":   7.0,
    "kitchen":        7.0,
    "sports":         5.0,
    "baby":           5.0,
    "garden":         7.0,
    "electronics":    3.0,
    "toys":           3.0,
}

# Default for unknown categories
DEFAULT_COMMISSION = 4.0

# Minimum product price (£) — skip cheap items
MIN_PRICE = 15.0

# ── User-Agent rotation ────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

def random_headers():
    """Return headers with a random User-Agent for scraping."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

def get_amazon_cookies():
    """Return cookies that force Amazon to display GBP prices."""
    return {
        "i18n-prefs": "GBP",
        "lc-acbuk": "en_GB"
    }

# ── Supabase helpers ───────────────────────────────────────

def supabase_get(path, params=None):
    """GET from Supabase REST API. Raises on HTTP error."""
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=SUPABASE_HEADERS,
        params=params or {}
    )
    resp.raise_for_status()
    return resp.json()

def supabase_post(path, data):
    """POST to Supabase REST API. Raises on HTTP error."""
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=SUPABASE_HEADERS,
        json=data
    )
    resp.raise_for_status()
    return resp

def supabase_patch(path, data):
    """PATCH Supabase REST API. Raises on HTTP error."""
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=SUPABASE_HEADERS,
        json=data
    )
    resp.raise_for_status()
    return resp

def get_commission(category):
    """Look up Amazon commission rate for a category."""
    return COMMISSION_RATES.get(category, DEFAULT_COMMISSION)
