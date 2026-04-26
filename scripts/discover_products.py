"""
discover_products.py
────────────────────
Scrapes Amazon UK bestseller pages for new products.

Improvements over v1:
- Uses shared config (no duplicated Supabase setup)
- Scrapes price → filters out cheap products (< £15)
- Sets commission from category rate table
- Rotates User-Agents to reduce bot detection
- Retry with backoff on failed requests
- Proper error logging instead of silent swallows
"""

import os, re, json, time, requests
from bs4 import BeautifulSoup
from config import (
    log, random_headers, get_commission, MIN_PRICE,
    supabase_get, supabase_post, supabase_patch, SUPABASE_HEADERS, SUPABASE_URL
)

# ── Gemini for niche/audience classification ──────
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    f"/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
) if GEMINI_KEY else None

# ── TARGET CATEGORIES ──────────────────────────────
CATEGORIES = {
    "fashion":        "https://www.amazon.co.uk/gp/bestsellers/fashion",
    "home_kitchen":   "https://www.amazon.co.uk/gp/bestsellers/kitchen",
    "beauty":         "https://www.amazon.co.uk/gp/bestsellers/beauty",
    "sports":         "https://www.amazon.co.uk/gp/bestsellers/sports",
}

MAX_RETRIES = 3
RETRY_BACKOFF = 3  # seconds, multiplied by attempt number


# ── FETCH EXISTING ASINS ───────────────────────────
def get_existing_asins():
    products = supabase_get("products", params={"select": "asin"})
    return {p["asin"] for p in products}


# ── PARSE PRICE ────────────────────────────────────
def parse_price(item):
    """Extract price from bestseller grid item. Returns float or None."""
    # Amazon UK bestseller pages show price in these selectors:
    #   span.a-color-price  →  "EUR€28.87" or "£28.87"
    #   span._cDEzb_p13n-sc-price_3mJ9Z  →  same format
    #   span.a-price span.a-offscreen  →  "£28.87" (product pages)
    for selector in [
        "span.a-color-price",
        "span[class*='p13n-sc-price']",
        "span.a-price span.a-offscreen",
    ]:
        price_tag = item.select_one(selector)
        if price_tag:
            raw = price_tag.text.strip()
            # Extract numeric value regardless of currency prefix (EUR€, £, etc.)
            match = re.search(r"([\d]+[.,]?\d*)", raw)
            if match:
                price_str = match.group(1).replace(",", "")
                try:
                    price = float(price_str)
                    log.debug(f"    Price parsed: {raw!r} → £{price}")
                    return price
                except ValueError:
                    log.debug(f"    Price parse failed: {raw!r}")
                    continue

    # Last fallback: whole + fraction spans (some layouts)
    price_whole = item.select_one("span.a-price-whole")
    price_frac = item.select_one("span.a-price-fraction")
    if price_whole:
        whole = price_whole.text.strip().rstrip(".")
        frac = price_frac.text.strip() if price_frac else "00"
        try:
            return float(f"{whole}.{frac}")
        except ValueError:
            pass

    log.debug(f"    No price found in item")
    return None


# ── FETCH PRICE FROM PRODUCT PAGE ──────────────────
def fetch_price_from_page(asin):
    """Fetch price directly from Amazon product page as fallback."""
    try:
        resp = requests.get(
            f"https://www.amazon.co.uk/dp/{asin}",
            headers=random_headers(),
            timeout=10
        )
        soup = BeautifulSoup(resp.text, "html.parser")

        # Product page price selectors
        for selector in [
            "span.a-price span.a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            "span.a-color-price",
            "span[class*='apexPriceToPay'] span.a-offscreen",
        ]:
            tag = soup.select_one(selector)
            if tag:
                match = re.search(r"([\d]+[.,]?\d*)", tag.text)
                if match:
                    return float(match.group(1).replace(",", ""))
    except Exception as e:
        log.warning(f"  Price page fetch failed for {asin}: {e}")
    return None


# ── BACKFILL PRICES FOR EXISTING PRODUCTS ──────────
def backfill_prices():
    """
    Update existing products that have null price.
    Fetches price from Amazon product page for each.
    """
    products = supabase_get("products", params={
        "active": "eq.true",
        "price": "is.null",
        "select": "id,asin,name"
    })

    if not products:
        log.info("No products need price backfill.")
        return 0

    log.info(f"Backfilling prices for {len(products)} products...")
    updated = 0

    for p in products:
        price = fetch_price_from_page(p["asin"])
        if price is not None:
            try:
                supabase_patch(f"products?id=eq.{p['id']}", {"price": price})
                log.info(f"  {p['name'][:50]} → £{price:.2f}")
                updated += 1
            except Exception as e:
                log.warning(f"  Failed to update {p['asin']}: {e}")
        else:
            log.warning(f"  Could not find price for {p['asin']}: {p['name'][:50]}")

        time.sleep(1)  # be nice to Amazon

    return updated


# ── SCRAPE BESTSELLER PAGE ─────────────────────────
def scrape_category(url, category):
    log.info(f"Scraping: {url}")

    # Retry with backoff
    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=random_headers(), timeout=15)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            wait = RETRY_BACKOFF * attempt
            log.warning(f"  Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                log.info(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"  All {MAX_RETRIES} attempts failed for {url}")
                return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.select("div.zg-grid-general-faceout")

    products = []
    skipped_cheap = 0

    for item in items:
        try:
            # Extract ASIN from link
            link_tag = item.find("a", href=True)
            if not link_tag:
                continue

            asin_match = re.search(r"/dp/([A-Z0-9]{10})", link_tag["href"])
            if not asin_match:
                continue

            asin = asin_match.group(1)

            # Extract title — multiple fallback selectors
            name = "Unknown"
            for selector in [
                "div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1",
                "div[class*='line-clamp']",
                "span.zg-text-center-align",
                "a span div",
            ]:
                title_tag = item.select_one(selector)
                if title_tag and title_tag.text.strip():
                    name = title_tag.text.strip()
                    break

            # Last resort: use link text
            if name == "Unknown" and link_tag.text.strip():
                name = link_tag.text.strip()[:200]

            # Extract price
            price = parse_price(item)

            # Filter cheap products
            if price is not None and price < MIN_PRICE:
                skipped_cheap += 1
                continue

            products.append({
                "asin": asin,
                "name": name,
                "price": price,
            })

        except Exception as e:
            log.warning(f"  Error parsing item: {e}")
            continue

    log.info(f"  Found {len(products)} products (skipped {skipped_cheap} under £{MIN_PRICE})")
    return products


# ── GEMINI NICHE/AUDIENCE CLASSIFICATION ───────────
def classify_products(products, category):
    """
    Use Gemini to classify niche + target audience for a batch of products.
    Returns dict mapping ASIN → {"niche": ..., "audience": ...}
    Falls back to category-based defaults if Gemini unavailable.
    """
    if not GEMINI_URL or not products:
        return {p["asin"]: _default_classification(category) for p in products}

    names = [f'- {p["asin"]}: {p["name"]}' for p in products]
    product_list = "\n".join(names)

    prompt = f"""You are a Pinterest marketing expert. Classify each product below into a
specific Pinterest niche and target audience.

Category: {category}

Products:
{product_list}

Return ONLY a valid JSON object mapping ASIN to classification, no markdown:
{{
  "B08N5WRWNW": {{
    "niche": "standing desk setup",
    "audience": "remote workers, freelancers who want ergonomic home offices"
  }}
}}

Rules:
- "niche" should be 2-4 words, specific enough for Pinterest SEO (NOT just the category)
- "audience" should be 1-2 sentences describing WHO would buy this and WHY
- Think about Pinterest search terms people actually use
- Be specific: "gym accessories for women" not "general shoppers"
- Include lifestyle context: who are they, what problem does this solve?"""

    try:
        resp = requests.post(
            GEMINI_URL,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30
        )
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = text.replace("```json", "").replace("```", "").strip()
        classifications = json.loads(text)

        log.info(f"  Classified {len(classifications)} products via Gemini")
        return classifications

    except Exception as e:
        log.warning(f"  Gemini classification failed: {e} — using defaults")
        return {p["asin"]: _default_classification(category) for p in products}


def _default_classification(category):
    """Fallback niche/audience when Gemini unavailable."""
    defaults = {
        "fashion":      {"niche": "fashion essentials",     "audience": "style-conscious shoppers looking for trending wardrobe pieces"},
        "home_kitchen": {"niche": "home organisation",      "audience": "homeowners and renters upgrading their living space"},
        "beauty":       {"niche": "skincare and beauty",    "audience": "beauty enthusiasts looking for effective products"},
        "sports":       {"niche": "fitness gear",           "audience": "active people and gym-goers looking for quality equipment"},
    }
    return defaults.get(category, {"niche": category.replace("_", " "), "audience": "shoppers interested in quality products"})


# ── INSERT INTO SUPABASE ───────────────────────────
def insert_products(products, category, classifications):
    inserted = 0
    commission = get_commission(category)

    for p in products:
        # Use Gemini classification or fallback
        cls = classifications.get(p["asin"], _default_classification(category))

        payload = {
            "asin": p["asin"],
            "name": p["name"],
            "category": category,
            "niche": cls["niche"],
            "audience": cls["audience"],
            "commission": commission,
            "active": True,
        }

        # Add price if scraped
        if p.get("price") is not None:
            payload["price"] = p["price"]

        try:
            supabase_post("products", payload)
            inserted += 1
        except requests.HTTPError as e:
            log.warning(f"  Error inserting {p['asin']}: {e}")

        time.sleep(0.2)

    return inserted


# ── MAIN ───────────────────────────────────────────
if __name__ == "__main__":
    log.info("=== PRODUCT DISCOVERY ===")

    existing = get_existing_asins()

    total_new = 0

    for category, url in CATEGORIES.items():
        scraped = scrape_category(url, category)

        # Filter already-known ASINs
        new_products = [p for p in scraped if p["asin"] not in existing]

        log.info(f"  New products: {len(new_products)}")

        if not new_products:
            continue

        # Classify niche + audience via Gemini
        classifications = classify_products(new_products, category)

        inserted = insert_products(new_products, category, classifications)

        log.info(f"  Inserted: {inserted}")

        total_new += inserted

        time.sleep(2)

    # Backfill prices for any existing products that have null price
    backfilled = backfill_prices()

    log.info(f"=== DONE — {total_new} new products added, {backfilled} prices backfilled ===")