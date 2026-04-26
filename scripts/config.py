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
    # 6.0%
    "fashion":        6.0,  # Custom key for discover_products
    "clothing":       6.0,  # Clothing & Accessories
    "shoes":          6.0,  # Shoes, Handbags, Wallets
    "watches":        6.0,
    "luxury":         6.0,
    "luxury_beauty":  6.0,  # Luxury stores beauty

    # 5.0%
    "amazon_instant_video": 5.0,
    "audible":        5.0,
    "automotive":     5.0,
    "books":          5.0,
    "digital_music":  5.0,
    "furniture":      5.0,
    "handmade":       5.0,
    "home":           5.0,
    "home_improvement": 5.0,
    "garden":         5.0,  # Often grouped with Home Improvement
    "jewellery":      5.0,
    "kindle_books":   5.0,
    "home_kitchen":   5.0,  # Custom key
    "kitchen":        5.0,  # Custom key
    "kitchen_dining": 5.0,
    "music":          5.0,
    "tools":          5.0,  # Power & Hand Tools

    # 4.0%
    "beauty":         4.0,
    "luggage":        4.0,
    "personal_care":  4.0,  # Personal Care Appliances
    "sports":         4.0,  # Custom key
    "sports_fitness": 4.0,

    # 2.5%
    "appliances":     2.5,
    "fire_tv":        2.5,
    "mobile_electronics": 2.5,

    # 1.0%
    "amazon_fresh":   1.0,
    "grocery":        1.0,
    "pantry":         1.0,
    "video_games":    1.0,
    "video_game_consoles": 1.0,

    # 0.0%
    "gift_cards":     0.0,
    "android_apps":   0.0,
    "kindle_unlimited": 0.0,
    "wine":           0.0,
    
    # Defaults
    "baby":           3.0,  # All Other Categories
    "electronics":    3.0,  # All Other Categories
    "toys":           3.0,  # All Other Categories
}

# Default for unknown categories ("All Other Categories")
DEFAULT_COMMISSION = 3.0

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
