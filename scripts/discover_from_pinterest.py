"""
discover_from_pinterest.py (V3 Pure Scraper Edition)
────────────────────────────────────────────────────
The "No-API" Affiliate Pipeline.

1.  DEMAND: Hits Pinterest Autocomplete (UK + US) to find what's trending.
2.  DISCOVERY: Scrapes Amazon Search results for those exact trends.
3.  EXTRACTION: Visits each product page and uses resilient scraping to get:
    - High-Res Images (via multi-selector & JSON parsing)
    - Real-Time Price
    - Best Sellers Rank (BSR)
4.  STORAGE: Saves to Supabase with full SEO metadata.

No Amazon API credentials required.
"""

import os, json, time, requests, re, random
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from config import (
    log, get_commission, random_headers, get_amazon_cookies,
    supabase_get, supabase_post, SUPABASE_HEADERS, SUPABASE_URL
)

# ── CONFIGURATION ───────────────────────────────────────────

AMAZON_TAG = os.environ.get("AMAZON_ASSOCIATE_TAG", "pinnpurchas0f-21") # Fallback if not set
MIN_PRICE  = 15.0

# Strings/markers that indicate Amazon served a bot-check / soft-block page
# instead of real content. Kept broad on purpose — Amazon changes wording
# over time and a missed marker here means silent, undiagnosable 0-result runs.
BLOCK_MARKERS = [
    "api-services-support@amazon.com",
    "To discuss automated access",
    "Sorry, we just need to make sure",
    "Enter the characters you see below",
    "/errors/validateCaptcha",
]

# Keyword noise filters — things that waste a request because they were
# never going to match real UK Amazon listings.
REJECT_KEYWORD_MARKERS = [
    "myntra", "meesho", "flipkart", "ajio",          # non-Amazon retailers
    " rs", "rs ", "₹", "inr", "rupees",               # Indian pricing/region
    "under 500", "under 1000", "under 2000",          # almost always ₹ price bands
    "prime video", "amazon prime", "tv show", "movie", # Prime Video drift
]

NICHES = [
    # Clothing - specific sub-niches that match real Pinterest searches
    {"name": "summer dresses women",       "category": "clothing",  "audience": "women looking for casual and going-out dresses"},
    {"name": "women's co-ord sets",        "category": "clothing",  "audience": "women who love matching outfit sets and effortless style"},
    {"name": "women's going out tops",     "category": "clothing",  "audience": "women looking for date night and night out outfits"},

    # Shoes - product-level specificity
    {"name": "white sneakers women",       "category": "shoes",     "audience": "women looking for clean everyday trainers"},
    {"name": "heeled boots women",         "category": "shoes",     "audience": "women who love ankle boots and heeled boots"},

    # Jewellery - trending styles
    {"name": "gold layered necklaces",     "category": "jewellery", "audience": "women who love dainty gold jewellery and layering"},
    {"name": "sterling silver rings women","category": "jewellery", "audience": "women looking for minimalist and stackable rings"},

    # Accessories - high-intent items
    {"name": "crossbody bags women",       "category": "fashion",   "audience": "women looking for everyday and going-out bags"},
]

# ── 1. PINTEREST DEMAND (UK + US) ──────────────────────────

def get_pinterest_trends(query, region="GB"):
    """
    Uses Google's Autocomplete API to find popular Pinterest trends.
    This is much more stable than hitting Pinterest directly.
    """
    log.info(f"  Fetching Pinterest trends via Google for: '{query}'")
    url = "https://suggestqueries.google.com/complete/search"
    params = {
        "client": "firefox",
        "q": query,
        "hl": "en-GB" if region == "GB" else "en-US",
        "gl": region.lower()
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Google returns [query, [suggestions], ...]
        if len(data) > 1:
            return data[1]
        return []
    except Exception as e:
        log.warning(f"    Trend research failed for '{query}': {e}")
        return []


def is_relevant_keyword(keyword):
    """Filter out keywords that were never going to return usable
    Amazon UK results (wrong region/currency, competitor sites, drift)."""
    k = keyword.lower()
    return not any(marker in k for marker in REJECT_KEYWORD_MARKERS)


def get_demand_keywords(niche):
    log.info(f"Researching global Pinterest demand for: {niche['name']}")

    seeds = [
        f"{niche['name']} outfit ideas",
        f"best {niche['name']} amazon",
        f"{niche['name']} trending {datetime.now().year}",
        f"{niche['name']} under £50",
        f"{niche['name']} pinterest",
    ]

    all_suggestions = []
    for region in ["GB", "US"]:
        for seed in seeds:
            suggestions = get_pinterest_trends(seed, region)
            all_suggestions.extend(suggestions)
            time.sleep(0.3)

    # Clean up: Remove the word "pinterest" and "ideas" from the keywords
    unique = list(dict.fromkeys(all_suggestions))
    clean = []
    for k in unique:
        k_clean = k.lower().replace("pinterest", "").replace("ideas", "").replace("  ", " ").strip()
        if len(k_clean) > 5 and is_relevant_keyword(k_clean):
            clean.append(k_clean)

    rejected = len(unique) - len(clean)
    clean = list(dict.fromkeys(clean))[:8]  # Top 8 global trends

    log.info(f"  Found {len(clean)} trending keywords ({rejected} filtered as noise): {clean}")
    return clean


# ── 2. AMAZON SESSION (shared, warmed-up) ───────────────────

def make_amazon_session():
    """
    A single persistent session reused across all Amazon requests in this run.
    A cookie-less, session-less request is one of the easiest signals for
    Amazon's bot detection to flag — warming up against the homepage first
    and reusing cookies materially reduces soft-block rates.
    """
    session = requests.Session()
    session.headers.update(random_headers())
    session.cookies.update(get_amazon_cookies())
    try:
        session.get("https://www.amazon.co.uk/", timeout=15)
        time.sleep(random.uniform(1.5, 3))
    except Exception as e:
        log.warning(f"  Amazon session warm-up failed (continuing anyway): {e}")
    return session


def is_blocked_response(resp):
    """Broad check for Amazon bot-check pages. A narrow single-string check
    means a changed block page slips through silently and looks like
    'zero organic results' instead of 'we got blocked'."""
    if resp.status_code in (202, 429, 503):
        return True
    text = resp.text
    return any(marker in text for marker in BLOCK_MARKERS)


# ── 3. AMAZON DISCOVERY (SEARCH SCRAPER) ─────────────────────

def scrape_amazon_search(keyword, session):
    """Scrapes Amazon.co.uk search results to find top products for a trend."""
    url = f"https://www.amazon.co.uk/s?k={requests.utils.quote(keyword)}"
    headers = random_headers()
    headers["Referer"] = "https://www.amazon.co.uk/"

    for attempt in range(1, 3):
        try:
            resp = session.get(url, headers=headers, timeout=15)

            if is_blocked_response(resp):
                if attempt == 1:
                    wait = random.uniform(6, 10)
                    log.warning(f"  Blocked/soft-blocked (status {resp.status_code}) for keyword: "
                                f"'{keyword}' — retrying in {wait:.1f}s with fresh headers")
                    session.headers.update(random_headers())
                    time.sleep(wait)
                    continue
                log.warning(f"  Still blocked after retry for keyword: '{keyword}' "
                            f"(status {resp.status_code}) — giving up on this keyword")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            asins = []

            # Find all product cards
            cards = soup.find_all("div", {"data-asin": True})
            for card in cards:
                asin = card["data-asin"]
                if not asin or len(asin) != 10: continue

                # Skip sponsored
                if "Sponsored" in card.get_text() or "AdHolder" in card.get("class", []):
                    continue

                asins.append(asin)
                if len(asins) >= 4: break # Get top 4 organic results

            if not asins:
                # IMPORTANT: this used to fail silently. Logging it makes it
                # possible to tell "genuinely no matches" apart from "we got
                # served a page we didn't recognise as a block".
                log.info(f"    No organic product cards found for: '{keyword}' "
                         f"(status {resp.status_code}, {len(resp.text)} chars, "
                         f"{len(cards)} raw cards)")

            return asins
        except Exception as e:
            log.error(f"  Amazon search failed for '{keyword}': {e}")
            return []

    return []


# ── 4. PRODUCT EXTRACTION (RESILIENT SCRAPER) ────────────────

def extract_product_details(asin, session):
    """Deep-scrapes a product page for high-res image, price, and BSR."""
    url = f"https://www.amazon.co.uk/dp/{asin}"
    headers = random_headers()
    headers["Referer"] = f"https://www.amazon.co.uk/s?k={asin}"

    product = {"asin": asin, "price": 0.0, "image_url": None, "bsr": None, "name": None}

    for attempt in range(1, 3):
        try:
            resp = session.get(url, headers=headers, timeout=15)
            if is_blocked_response(resp):
                wait = random.uniform(5, 8)
                log.warning(f"  Blocked (status {resp.status_code}) extracting {asin}, "
                            f"retrying in {wait:.1f}s...")
                session.headers.update(random_headers())
                time.sleep(wait)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # 1. Title
            title_tag = soup.find(id="productTitle")
            if title_tag: product["name"] = title_tag.get_text(strip=True)[:150]

            # 2. Price
            price_span = soup.select_one(".a-price .a-offscreen") or soup.select_one(".a-price-whole")
            if price_span:
                price_text = re.sub(r"[^\d.]", "", price_span.get_text())
                if price_text: product["price"] = float(price_text)

            # 3. Image (Resilient logic)
            img_tag = soup.find("img", id="landingImage") or soup.find("img", id="main-image")
            if img_tag and img_tag.get("data-old-hires"):
                product["image_url"] = img_tag["data-old-hires"]
            elif img_tag and img_tag.get("data-a-dynamic-image"):
                # Parse JSON dict of images {url: [w,h]}
                try:
                    img_dict = json.loads(img_tag["data-a-dynamic-image"])
                    # Pick the largest one
                    product["image_url"] = max(img_dict.items(), key=lambda x: x[1][0] * x[1][1])[0]
                except: pass

            # 4. BSR (Resilient logic)
            bsr_label = soup.find(string=re.compile(r"Best\s*Sellers?\s*Rank", re.I))
            if bsr_label:
                container = bsr_label.find_parent(["span", "li", "td", "div"])
                if container:
                    m = re.search(r"#([\d,]+)\s+in\s+", container.get_text())
                    if m: product["bsr"] = int(m.group(1).replace(",", ""))

            # Success!
            if product["name"] and product["price"] > 0:
                return product

            if not product["name"]:
                log.info(f"    No title found for {asin} (status {resp.status_code}, "
                         f"{len(resp.text)} chars) — likely blocked or unusual page layout")

        except Exception as e:
            log.warning(f"  Attempt {attempt} failed for {asin}: {e}")
            time.sleep(2)

    return None


# ── MAIN PIPELINE ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"V3 Pinterest-First (Scraper Mode) — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    # 1. Load existing ASINs to avoid duplicates
    existing_asins = {p["asin"] for p in supabase_get("products", params={"select": "asin"})}
    total_added = 0

    # Single warmed-up, cookie-persistent session reused for every Amazon
    # request this run — see make_amazon_session() for why this matters.
    amazon_session = make_amazon_session()

    for niche in NICHES:
        print(f"\nNICHE: {niche['name'].upper()}")

        # Step 1: Find Demand
        keywords = get_demand_keywords(niche)

        for kw in keywords:
            print(f"  Trend: '{kw}'")

            # Step 2: Search Amazon
            asins = scrape_amazon_search(kw, amazon_session)

            for asin in asins:
                if asin in existing_asins: continue

                # Step 3: Deep Extract
                print(f"    Extracting {asin}...", end=" ", flush=True)
                p_data = extract_product_details(asin, amazon_session)

                if p_data and p_data["price"] >= MIN_PRICE and p_data["image_url"]:
                    # Build final product object
                    product = {
                        "asin":          asin,
                        "name":          p_data["name"],
                        "category":      niche["category"],
                        "niche":         niche["name"],
                        "audience":      niche["audience"],
                        "commission":    get_commission(niche["category"]),
                        "price":         p_data["price"],
                        "image_url":     p_data["image_url"],
                        "affiliate_url": f"https://www.amazon.co.uk/dp/{asin}?tag={AMAZON_TAG}&linkCode=ll2",
                        "pinterest_keywords": [kw],
                        "active":        True,
                        "bsr_rank":      p_data.get("bsr"),
                        "keywords_last_updated_at": datetime.now(timezone.utc).isoformat()
                    }

                    try:
                        supabase_post("products", product)
                        print(f"✓ ADDED (£{product['price']})")
                        existing_asins.add(asin)
                        total_added += 1
                    except Exception as e:
                        print(f"✗ DB ERROR: {e}")
                        # Mark ASIN as 'existing' anyway so we don't spam errors for the same product
                        existing_asins.add(asin)
                else:
                    print("SKIPPED (Missing data or < £15)")

                time.sleep(random.uniform(2, 4)) # Jitter between products

    print("\n" + "=" * 60)
    print(f"Done. Added {total_added} products discovered via Pinterest Trends.")
    print("=" * 60)
