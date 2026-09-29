"""Refresh active product prices from public Amazon UK product pages.

This job deliberately does not use the Creators API. It only writes a price
when the live page provides a valid GBP offer price, so a blocked page or a
foreign-currency response cannot replace a value already in Supabase.
"""

import os
import random
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests
from bs4 import BeautifulSoup

from config import get_amazon_cookies, log, random_headers, supabase_get, supabase_patch


MAX_RETRIES = 3
REQUEST_DELAY_SECONDS = float(os.environ.get("PRICE_REFRESH_DELAY_SECONDS", "2.5"))
MAX_PRICE = Decimal("5000.00")
MIN_PRICE = Decimal("0.50")
GBP_PRICE_PATTERN = re.compile(r"\u00a3\s*([\d,]+(?:\.\d{1,2})?)")

BLOCK_MARKERS = (
    "api-services-support@amazon.com",
    "to discuss automated access",
    "sorry, we just need to make sure",
    "enter the characters you see below",
    "/errors/validatecaptcha",
    "robot check",
)

PRICE_SELECTORS = (
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-price:not(.a-text-price) .a-offscreen",
    "#corePrice_desktop .priceToPay .a-price:not(.a-text-price) .a-offscreen",
    "#apex_desktop .priceToPay .a-price:not(.a-text-price) .a-offscreen",
    "#buybox .a-price:not(.a-text-price) .a-offscreen",
    ".priceToPay .a-offscreen",
    "#priceblock_dealprice",
    "#priceblock_ourprice",
    "span.a-price:not(.a-text-price) .a-offscreen",
)


def extract_current_gbp_price(soup):
    """Return the current GBP offer price, excluding crossed-out list prices."""
    for selector in PRICE_SELECTORS:
        for price_tag in soup.select(selector):
            text = price_tag.get_text(" ", strip=True)
            match = GBP_PRICE_PATTERN.search(text)
            if not match:
                continue

            try:
                price = Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                continue

            if MIN_PRICE <= price <= MAX_PRICE:
                return price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return None


def is_blocked_response(response):
    body = response.text.lower()
    return response.status_code in (202, 429, 503) or any(marker in body for marker in BLOCK_MARKERS)


def fetch_current_price(asin, session):
    """Fetch one ASIN and return its valid GBP price, or None without writing."""
    url = f"https://www.amazon.co.uk/dp/{asin}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            headers = random_headers()
            headers["Referer"] = "https://www.amazon.co.uk/"
            response = session.get(url, headers=headers, timeout=15)

            if is_blocked_response(response):
                log.warning("Amazon blocked price refresh for %s (attempt %s/%s)", asin, attempt, MAX_RETRIES)
            else:
                response.raise_for_status()
                price = extract_current_gbp_price(BeautifulSoup(response.text, "html.parser"))
                if price is not None:
                    return price
                log.info("No GBP offer price found for %s (attempt %s/%s)", asin, attempt, MAX_RETRIES)
        except requests.RequestException as error:
            log.warning("Price refresh request failed for %s (attempt %s/%s): %s", asin, attempt, MAX_RETRIES, error)

        if attempt < MAX_RETRIES:
            time.sleep(attempt * 3)

    return None


def load_products(limit):
    products = supabase_get("products", params={
        "active": "eq.true",
        "select": "id,asin,name,price",
        "order": "id.asc",
    })
    return products[:limit] if limit else products


def stored_price_matches(stored_price, current_price):
    try:
        stored = Decimal(str(stored_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return stored == current_price


def refresh_prices(products, dry_run):
    session = requests.Session()
    session.headers.update(random_headers())
    session.cookies.update(get_amazon_cookies())

    changed = unchanged = missing = 0
    for index, product in enumerate(products, start=1):
        asin = product["asin"]
        price = fetch_current_price(asin, session)
        if price is None:
            missing += 1
            print(f"[{index}/{len(products)}] {asin}: no valid GBP price; left unchanged")
        elif stored_price_matches(product.get("price"), price):
            unchanged += 1
            print(f"[{index}/{len(products)}] {asin}: GBP {price} unchanged")
        elif dry_run:
            changed += 1
            print(f"[{index}/{len(products)}] {asin}: would update GBP {product.get('price')} -> {price}")
        else:
            supabase_patch(f"products?id=eq.{product['id']}", {"price": float(price)})
            changed += 1
            print(f"[{index}/{len(products)}] {asin}: updated GBP {product.get('price')} -> {price}")

        if index < len(products):
            time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0, 1))

    return changed, unchanged, missing


def main():
    try:
        limit = int(os.environ.get("PRICE_REFRESH_LIMIT", "0"))
    except ValueError as error:
        raise ValueError("PRICE_REFRESH_LIMIT must be a whole number") from error
    if limit < 0:
        raise ValueError("PRICE_REFRESH_LIMIT cannot be negative")

    dry_run = os.environ.get("PRICE_REFRESH_DRY_RUN", "0") == "1"
    products = load_products(limit)
    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"{mode}: refreshing prices for {len(products)} active product(s)")
    if not products:
        return

    changed, unchanged, missing = refresh_prices(products, dry_run)
    print(f"{mode} complete: {changed} changed, {unchanged} unchanged, {missing} without a GBP price")

    if missing == len(products):
        raise RuntimeError("Amazon returned no usable GBP prices; no product prices were changed")


if __name__ == "__main__":
    main()
