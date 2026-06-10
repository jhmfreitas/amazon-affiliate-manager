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

NICHES = [
    {"name": "home aesthetic", "category": "home", "audience": "homeowners, renters, decor enthusiasts"},
    {"name": "affordable fashion", "category": "apparel", "audience": "fashion enthusiasts, budget shoppers"},
    {"name": "skincare routine", "category": "beauty", "audience": "beauty enthusiasts"},
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
        "q": f"{query} pinterest",
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

def get_demand_keywords(niche):
    log.info(f"Researching global Pinterest demand for: {niche['name']}")
    
    seeds = [
        f"{niche['name']} ideas",
        f"best {niche['name']}",
        f"{niche['name']} aesthetic",
        f"{niche['name']} must haves",
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
        if len(k_clean) > 5:
            clean.append(k_clean)
            
    clean = list(dict.fromkeys(clean))[:8] # Top 8 global trends
    log.info(f"  Found {len(clean)} trending keywords: {clean}")
    return clean


# ── 2. AMAZON DISCOVERY (SEARCH SCRAPER) ─────────────────────

def scrape_amazon_search(keyword):
    """Scrapes Amazon.co.uk search results to find top products for a trend."""
    url = f"https://www.amazon.co.uk/s?k={requests.utils.quote(keyword)}"
    headers = random_headers()
    headers["Referer"] = "https://www.amazon.co.uk/"
    
    try:
        resp = requests.get(url, headers=headers, cookies=get_amazon_cookies(), timeout=15)
        if "api-services-support@amazon.com" in resp.text:
            log.warning(f"  Search blocked (CAPTCHA) for keyword: {keyword}")
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
            
        return asins
    except Exception as e:
        log.error(f"  Amazon search failed for '{keyword}': {e}")
        return []


# ── 3. PRODUCT EXTRACTION (RESILIENT SCRAPER) ────────────────

def extract_product_details(asin):
    """Deep-scrapes a product page for high-res image, price, and BSR."""
    url = f"https://www.amazon.co.uk/dp/{asin}"
    headers = random_headers()
    headers["Referer"] = f"https://www.amazon.co.uk/s?k={asin}"
    
    product = {"asin": asin, "price": 0.0, "image_url": None, "bsr": None, "name": None}
    
    for attempt in range(1, 3):
        try:
            resp = requests.get(url, headers=headers, cookies=get_amazon_cookies(), timeout=15)
            if "api-services-support@amazon.com" in resp.text:
                time.sleep(random.uniform(5, 8))
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
    
    for niche in NICHES:
        print(f"\nNICHE: {niche['name'].upper()}")
        
        # Step 1: Find Demand
        keywords = get_demand_keywords(niche)
        
        for kw in keywords:
            print(f"  Trend: '{kw}'")
            
            # Step 2: Search Amazon
            asins = scrape_amazon_search(kw)
            
            for asin in asins:
                if asin in existing_asins: continue
                
                # Step 3: Deep Extract
                print(f"    Extracting {asin}...", end=" ", flush=True)
                p_data = extract_product_details(asin)
                
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
