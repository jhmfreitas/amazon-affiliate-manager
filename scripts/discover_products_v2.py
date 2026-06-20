"""
discover_products_v2.py
───────────────────────
The V2 "Demand-First" Affiliate Pipeline.

1. Takes seed niches (e.g. "home office").
2. Asks Google Autocomplete what Pinterest users are actually searching for.
3. Passes those EXACT long-tail keywords to the Amazon Creators API.
4. Grabs the top converting Amazon products for that exact intent.
5. Saves the product, price, and High-Resolution Amazon Image URL to Supabase.
"""

import os, json, time, requests
from datetime import datetime, timezone

from config import (
    log, get_commission, 
    supabase_get, supabase_post, supabase_patch, SUPABASE_HEADERS, SUPABASE_URL
)

# Secrets
AMAZON_CRED_ID  = os.environ["AMAZON_CREDENTIAL_ID"]
AMAZON_CRED_SEC = os.environ["AMAZON_CREDENTIAL_SECRET"]
AMAZON_TAG      = os.environ.get("AMAZON_ASSOCIATE_TAG", "pinnpurchas0f-21")

try:
    from amazon_creatorsapi import AmazonCreatorsApi, Country
    from amazon_creatorsapi.models import SearchItemsResource
    from creatorsapi_python_sdk.exceptions import ApiException
except ImportError:
    log.error("amazon_creatorsapi not found! Run: pip install python-amazon-paapi")
    exit(1)


# ── NICHES & TARGETS ─────────────────────────────────────────

NICHES = [
    # Clothing – specific sub-niches that match real Pinterest searches
    {"name": "summer dresses women",       "category": "clothing",  "audience": "women looking for casual and going-out dresses"},
    {"name": "women's co-ord sets",        "category": "clothing",  "audience": "women who love matching outfit sets and effortless style"},
    {"name": "women's going out tops",     "category": "clothing",  "audience": "women looking for date night and night out outfits"},

    # Shoes – product-level specificity
    {"name": "white sneakers women",       "category": "shoes",     "audience": "women looking for clean everyday trainers"},
    {"name": "heeled boots women",         "category": "shoes",     "audience": "women who love ankle boots and heeled boots"},

    # Jewellery – trending styles
    {"name": "gold layered necklaces",     "category": "jewellery", "audience": "women who love dainty gold jewellery and layering"},
    {"name": "sterling silver rings women","category": "jewellery", "audience": "women looking for minimalist and stackable rings"},

    # Accessories – high-intent items
    {"name": "crossbody bags women",       "category": "fashion",   "audience": "women looking for everyday and going-out bags"},
]

# We want products with deals, high ratings, and Prime shipping.
MIN_REVIEWS_RATING = 4


# ── 1. KEYWORD RESEARCH (DEMAND) ─────────────────────────────

def google_autocomplete(query, lang="en", country="uk"):
    try:
        resp = requests.get(
            "https://suggestqueries.google.com/complete/search",
            params={"client": "firefox", "q": query, "hl": lang, "gl": country},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        return data[1] if len(data) > 1 else []
    except Exception as e:
        log.warning(f"Google autocomplete failed for '{query}': {e}")
        return []

def get_demand_keywords(niche):
    log.info(f"Researching demand for niche: {niche['name']}")
    seeds = [
        f"{niche['name']} outfit ideas",
        f"best {niche['name']} amazon",
        f"{niche['name']} trending 2025",
        f"{niche['name']} under £50",
        f"{niche['name']} pinterest",
    ]
    
    all_suggestions = []
    for seed in seeds:
        suggestions = google_autocomplete(seed)
        all_suggestions.extend(suggestions)
        time.sleep(0.3)
        
    unique = list(dict.fromkeys(all_suggestions))
    
    # Clean up (remove "pinterest" from the search term for Amazon)
    clean_keywords = [k.replace(" pinterest", "").replace(" ideas", "").strip() for k in unique]
    clean_keywords = list(dict.fromkeys(clean_keywords))[:5] # Top 5 distinct searches
    
    log.info(f"Found {len(clean_keywords)} high-intent searches: {clean_keywords}")
    return clean_keywords


# ── 2. AMAZON API (DISCOVERY) ────────────────────────────────

def discover_products(keywords, niche):
    api = AmazonCreatorsApi(
        credential_id=AMAZON_CRED_ID.strip(),
        credential_secret=AMAZON_CRED_SEC.strip(),
        version="3.2",
        tag=AMAZON_TAG.strip(),
        country="UK",
        throttling=1.5
    )
    
    discovered = []
    existing_asins = {p["asin"] for p in supabase_get("products", params={"select": "asin"})}
    
    for kw in keywords:
        log.info(f"Searching Amazon for: '{kw}'")
        try:
            result = api.search_items(
                keywords=kw,
                item_count=3,
                min_reviews_rating=MIN_REVIEWS_RATING
            )
            
            for item in result.items:
                if item.asin in existing_asins:
                    continue
                
                # Extract Title
                title = item.item_info.title.display_value if item.item_info and item.item_info.title else "Unknown Product"
                
                # Extract Price
                price = 0.0
                if item.offers and item.offers.listings:
                    price_info = item.offers.listings[0].price
                    if price_info and price_info.amount:
                        price = price_info.amount
                
                # Skip cheap junk
                if price < 15.0:
                    continue
                    
                # Extract Image URL (Prefer High Res, fallback to Large)
                image_url = None
                if item.images and item.images.primary:
                    if item.images.primary.high_res:
                        image_url = item.images.primary.high_res.url
                    elif item.images.primary.large:
                        image_url = item.images.primary.large.url
                        
                if not image_url:
                    continue
                
                # Build Affiliate URL
                affiliate_url = f"https://www.amazon.co.uk/dp/{item.asin}?tag={AMAZON_TAG.strip()}&linkCode=ll2"
                
                product = {
                    "asin": item.asin,
                    "name": title[:150], # Truncate long titles
                    "category": niche["category"],
                    "niche": niche["name"],
                    "audience": niche["audience"],
                    "commission": get_commission(niche["category"]),
                    "price": price,
                    "image_url": image_url,
                    "affiliate_url": affiliate_url,
                    "pinterest_keywords": [kw] # The exact search term we used!
                }
                
                discovered.append(product)
                existing_asins.add(item.asin)
                
        except Exception as e:
            log.warning(f"Search failed for '{kw}': {e}")
            
    return discovered


# ── 3. DATABASE (STORAGE) ────────────────────────────────────

def insert_products(products):
    if not products:
        log.info("No new products to insert.")
        return 0
        
    log.info(f"Inserting {len(products)} new products into Supabase...")
    inserted = 0
    for p in products:
        try:
            supabase_post("products", p)
            log.info(f"  ✓ Added: {p['name'][:50]}... (£{p['price']})")
            inserted += 1
        except Exception as e:
            log.warning(f"  ✗ Failed to add {p['asin']}: {e}")
            
    return inserted


# ── MAIN PIPELINE ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"V2 Demand-First Discovery — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)
    
    total_discovered = []
    
    for niche in NICHES:
        print(f"\nProcessing Niche: {niche['name'].upper()}")
        
        # 1. Discover Demand
        keywords = get_demand_keywords(niche)
        
        # 2. Search Amazon API
        products = discover_products(keywords, niche)
        total_discovered.extend(products)
        
    print("\n" + "=" * 60)
    
    # 3. Store Results
    inserted = insert_products(total_discovered)
    
    print(f"\nDone. Discovered and inserted {inserted} highly-targeted products.")
