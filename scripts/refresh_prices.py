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
    "type the characters you see in this image",
    "/errors/validatecaptcha",
    "robot check",
    "automated access",
)

PRICE_SELECTORS = (
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-price:not(.a-text-price) .a-offscreen",
    "#corePriceDisplay_desktop_feature_div span.a-price:not(.a-text-price) .a-offscreen",
    "#corePrice_feature_div span.a-price:not(.a-text-price) .a-offscreen",
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
    return (
        response.status_code in (202, 429, 503)
        or any(marker in body for marker in BLOCK_MARKERS)
        or "captcha" in body
        or "sorry! something went wrong" in body
    )


def fetch_current_price(asin, session):
    """Fetch one ASIN and return its valid GBP price, or None without writing."""
    url = f"https://www.amazon.co.uk/dp/{asin}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req_headers = {
                "Referer": f"https://www.amazon.co.uk/s?k={asin}",
            }
            response = session.get(url, headers=req_headers, timeout=15)

            if is_blocked_response(response):
                log.warning("Amazon blocked price refresh for %s (attempt %s/%s)", asin, attempt, MAX_RETRIES)
            else:
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                title = soup.title.get_text(strip=True) if soup.title else ""

                if "robot check" in title.lower() or "service unavailable" in title.lower() or soup.select_one("form[action*='validateCaptcha']"):
                    log.warning("Amazon served CAPTCHA/Robot Check for %s (attempt %s/%s)", asin, attempt, MAX_RETRIES)
                    if attempt < MAX_RETRIES:
                        time.sleep(attempt * 3)
                    continue

                price = extract_current_gbp_price(soup)
                if price is not None:
                    return price

                # Check for explicit out of stock / unavailable
                avail = soup.select_one("#availability, #outOfStock")
                avail_text = avail.get_text(" ", strip=True) if avail else None
                if avail_text and "currently unavailable" in avail_text.lower():
                    log.info("Product %s is currently unavailable / out of stock on Amazon", asin)
                    return None

                # Search for non-GBP price in price tags (avoiding javascript $. / scripts)
                foreign = None
                for price_tag in soup.select(".a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice"):
                    t = price_tag.get_text(strip=True)
                    if re.search(r"(?:EUR|€|USD|\$)\s*\d", t):
                        foreign = t
                        break

                if foreign:
                    log.info(
                        "No GBP offer price found for %s (detected non-GBP price '%s') (attempt %s/%s)",
                        asin, foreign, attempt, MAX_RETRIES
                    )
                else:
                    log.info(
                        "No GBP offer price found for %s (title=%r, len=%d) (attempt %s/%s)",
                        asin, title[:60], len(response.text), attempt, MAX_RETRIES
                    )
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
    # Keep one consistent User-Agent across the entire session to avoid bot flagging
    session.headers.update(random_headers())
    cookies = get_amazon_cookies()
    session.cookies.update(cookies)
    for k, v in cookies.items():
        session.cookies.set(k, v, domain=".amazon.co.uk")

    try:
        log.info("Warming up Amazon UK session...")
        session.get("https://www.amazon.co.uk/", timeout=15)
        time.sleep(1.5)
    except Exception as e:
        log.warning("Amazon session warm-up request failed: %s", e)

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
