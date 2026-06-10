"""
score_products.py
─────────────────
Runs weekly (Monday 7am UTC) via GitHub Actions.

For each active product:
  1. Fetches Amazon BSR, price, availability via scraping
  2. Fetches Google Trends interest score via pytrends
  3. Reads Pinterest metrics (impressions, clicks, saves)
  4. Scores each product 0-100 using a deterministic formula
  5. Auto-pauses underperforming products
  6. Saves scores back to Supabase

Scoring weights: Pinterest Performance 40%, Revenue Potential 25%,
Market Demand 20%, Momentum 15%. No LLM dependency.
"""

import os, json, time, re, requests, random
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from pinterest_auth import PinterestAuth
from config import (
    log, random_headers, get_amazon_cookies, 
    supabase_get, supabase_patch, SUPABASE_HEADERS, SUPABASE_URL
)

# ── Secrets ────────────────────────────────────────────────────────────
GEMINI_KEY   = os.environ.get("GEMINI_API_KEY", "")  # Only used for keyword research
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    f"/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
) if GEMINI_KEY else None

SUPABASE_HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

PINTEREST_API = "https://api.pinterest.com/v5"

# Amazon Associates UK commission rates by category
# Source: affiliate-program.amazon.co.uk/help/node/topic/GRXPHT8U84RAYDXZ
# Last updated: April 2026
COMMISSION_RATES = {
    # 6% — Fashion, Luxury, Accessories
    "amazon fashion":            6.0,
    "clothing":                  6.0,
    "clothing & accessories":    6.0,
    "fashion":                   6.0,
    "luxury":                    6.0,
    "luxury beauty":             6.0,
    "luxury stores beauty":      6.0,
    "luxury stores fashion":     6.0,
    "shoes":                     6.0,
    "handbags":                  6.0,
    "wallets":                   6.0,
    "watches":                   6.0,

    # 5% — Home, Kitchen, Books, Music, Automotive, Tools
    "amazon instant video":      5.0,
    "audible":                   5.0,
    "audiobooks":                5.0,
    "automotive":                5.0,
    "books":                     5.0,
    "digital music":             5.0,
    "furniture":                 5.0,
    "handmade":                  5.0,
    "home":                      5.0,
    "home office":               5.0,
    "home improvement":          5.0,
    "jewellery":                 5.0,
    "jewelry":                   5.0,
    "kindle books":              5.0,
    "kitchen":                   5.0,
    "kitchen & dining":          5.0,
    "music":                     5.0,
    "power tools":               5.0,
    "hand tools":                5.0,
    "tools":                     5.0,

    # 4% — Beauty, Sports, Luggage
    "beauty":                    4.0,
    "luggage":                   4.0,
    "personal care appliances":  4.0,
    "sports":                    4.0,
    "sports & fitness":          4.0,
    "fitness":                   4.0,
    "outdoors":                  4.0,

    # 2.5% — Electronics, Appliances
    "appliances":                2.5,
    "fire tv":                   2.5,
    "mobile electronics":        2.5,
    "electronics":               2.5,
    "headphones":                2.5,
    "pc":                        2.5,
    "computers":                 2.5,

    # 1% — Grocery, Gaming, Fresh
    "amazon fresh":              1.0,
    "grocery":                   1.0,
    "pantry":                    1.0,
    "video games":               1.0,
    "video game consoles":       1.0,
    "gaming":                    1.0,

    # 0% — Gift cards, wine, apps
    "gift cards":                0.0,
    "gift card":                 0.0,
    "kindle unlimited":          0.0,
    "wine":                      0.0,
    "android apps":              0.0,
    "coach":                     0.0,

    # Default for all other categories (e.g. toys, baby, pets, office)
    "default":                   3.0,
}


# ── 1. Load active products ──────────────────────────────────

def load_products():
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=SUPABASE_HEADERS,
        params={"active": "eq.true", "order": "id.asc"}
    )
    resp.raise_for_status()
    products = resp.json()
    print(f"Loaded {len(products)} active products.")
    return products


# ── 2. Amazon BSR scraper ────────────────────────────────────

def get_amazon_signals(asin, session=None):
    """Fetch BSR rank, price, and availability from Amazon."""
    url = f"https://www.amazon.co.uk/dp/{asin}"
    if not session:
        session = requests.Session()
        session.headers.update(random_headers())

    signals = {"bsr": None, "price": None, "available": True}

    for attempt in range(1, 4):
        try:
            headers = random_headers()
            headers["Referer"] = f"https://www.amazon.co.uk/s?k={asin}"
            resp = session.get(url, headers=headers, timeout=15)
            
            if resp.status_code == 200:
                if "api-services-support@amazon.com" in resp.text:
                    time.sleep(random.uniform(5, 10))
                    continue
                
                soup = BeautifulSoup(resp.text, "html.parser")
                
                # 1. Availability
                out_of_stock = soup.find(id="outOfStock") or soup.find(id="availability")
                if out_of_stock and "currently unavailable" in out_of_stock.get_text().lower():
                    signals["available"] = False
                
                # 2. Price
                price_span = soup.select_one(".a-price .a-offscreen") or soup.select_one(".a-price-whole")
                if price_span:
                    try:
                        price_text = re.sub(r"[^\d.]", "", price_span.get_text())
                        if price_text: signals["price"] = float(price_text)
                    except: pass

                # 3. BSR extraction (Amazon UK format)
                # UK pages use table.prodDetTable with format: "6,032 in Home & Kitchen" (no # symbol)
                
                # Method 1: prodDetTable (most common on Amazon UK)
                prod_table = soup.select_one("table.prodDetTable")
                if prod_table:
                    bsr_th = prod_table.find("th", string=re.compile(r"Best\s*Sellers?\s*Rank", re.I))
                    if bsr_th:
                        bsr_td = bsr_th.find_next("td")
                        if bsr_td:
                            # Match with or without # prefix: "6,032 in" or "#6,032 in"
                            match = re.search(r"#?([\d,]+)\s+in\s", bsr_td.get_text())
                            if match:
                                signals["bsr"] = int(match.group(1).replace(",", ""))
                                print(f"  BSR: {signals['bsr']} (prodDetTable)")
                                return signals

                # Method 2: Any th/td with "Best Sellers Rank" text
                table_cell = soup.find(["th", "td"], string=re.compile(r"Best\s*Sellers?\s*Rank", re.I))
                if table_cell:
                    value_cell = table_cell.find_next("td") or table_cell
                    match = re.search(r"#?([\d,]+)", value_cell.get_text(strip=True))
                    if match:
                        signals["bsr"] = int(match.group(1).replace(",", ""))
                        print(f"  BSR: {signals['bsr']} (table cell)")
                        return signals

                # Method 3: Raw text search (fallback)
                match = re.search(r"Best\s*Sellers?\s*Rank[:\s]*#?([\d,]+)", resp.text, re.I)
                if match:
                    signals["bsr"] = int(match.group(1).replace(",", ""))
                    print(f"  BSR: {signals['bsr']} (raw text)")
                    return signals
                
                # Method 4: detailBullets format (some product types)
                bullets = soup.find(id="detailBulletsWrapper_feature_div")
                if bullets:
                    match = re.search(r"#?([\d,]+)\s+in\s", bullets.get_text())
                    if match:
                        signals["bsr"] = int(match.group(1).replace(",", ""))
                        print(f"  BSR: {signals['bsr']} (detailBullets)")
                        return signals

                print(f"  BSR: Not found for {asin}")
                return signals
            
            elif resp.status_code == 404:
                signals["available"] = False
                return signals

        except Exception as e:
            time.sleep(2)
            
    return signals



# ── 3. Google Trends ─────────────────────────────────────────

TRENDS_RATE_LIMITED = False

def get_trend_score(keyword, fallback_niche=None):
    """Get interest score (0.0-1.0) and direction for a keyword."""
    global TRENDS_RATE_LIMITED
    
    if TRENDS_RATE_LIMITED:
        print("  Trends: Skipped (Global Rate Limit) — score=50.0 direction=stable")
        return 50.0, "stable"
        
    search_term = keyword
    # If the keyword is too long (Amazon titles), it fails on Trends.
    # We should prioritize shorter, high-intent keywords.
    if len(search_term.split()) > 6:
        search_term = " ".join(search_term.split()[:4])
        
    print(f"  Trends: Researching: '{search_term}'", end=" ", flush=True)
    import random
    for attempt in range(1, 4):
        try:
            from pytrends.request import TrendReq
            # Pass random user agent to avoid quick blocks
            pt = TrendReq(hl="en-GB", tz=0, timeout=(15, 30), requests_args={"headers": random_headers()})
            
            # If second attempt, wait longer
            if attempt > 1:
                time.sleep(random.uniform(15, 25))
            else:
                time.sleep(random.uniform(4, 8))
            
            pt.build_payload([search_term], timeframe="now 7-d", geo="GB")
            data = pt.interest_over_time()

            if data.empty or data[search_term].sum() < 5.0:
                # If no data or very low interest, try a broader term (first 2 words)
                broader = " ".join(search_term.split()[:2])
                if broader != search_term:
                    print(f" (low/no data, trying '{broader}'...)", end="", flush=True)
                    pt.build_payload([broader], timeframe="now 7-d", geo="GB")
                    data = pt.interest_over_time()
            
            if data.empty or data.columns[0] not in data:
                print(f"  Trends: score=50.0 direction=stable (no data)")
                return 50.0, "stable"

            values = data[data.columns[0]].tolist()
            avg    = sum(values) / len(values)
            mid    = len(values) // 2
            first  = sum(values[:mid]) / max(mid, 1)
            second = sum(values[mid:]) / max(len(values) - mid, 1)

            if second > first * 1.15:
                direction = "rising"
            elif second < first * 0.85:
                direction = "falling"
            else:
                direction = "stable"

            print(f"  Trends: score={avg:.1f} direction={direction}")
            return round(avg, 1), direction

        except Exception as e:
            if "429" in str(e):
                if attempt == 1:
                    continue # Try one more time
                print(f"  Trends: 429 Rate Limit hit — using default and skipping for remaining products.")
                TRENDS_RATE_LIMITED = True
            else:
                print(f"  Trends error: {e}")
            return 50.0, "stable"
    return 50.0, "stable"


# ── 4. Pinterest pin saves for this product ──────────────────

def get_pinterest_saves(product_id, auth: PinterestAuth):
    """
    Sum saves across all posted pins for this product.
    Returns (total_saves, valid_pin_count) tuple.
    Uses shared PinterestAuth — auto-refreshes token on 401.
    """
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/pins",
            headers=SUPABASE_HEADERS,
            params={
                "product_id": f"eq.{product_id}",
                "posted":     "eq.true",
                "select":     "id,pinterest_id"
            }
        )
        resp.raise_for_status()
        pins = resp.json()

        if not pins:
            print(f"  Pinterest saves: no posted pins yet")
            return 0, 0

        total_saves = 0
        valid_pins  = 0
        for pin in pins:
            pid = pin.get("pinterest_id")
            if not pid:
                continue
            try:
                # Use auth.get() — handles 401 + token refresh automatically
                r = auth.get(
                    f"{PINTEREST_API}/pins/{pid}",
                    params={"pin_metrics": "true"}
                )
                if r.ok:
                    metrics     = r.json().get("pin_metrics", {})
                    saves       = metrics.get("lifetime_metrics", {}).get("save", 0)
                    total_saves += saves
                    valid_pins  += 1
                elif r.status_code == 404:
                    print(f"  Pin {pid} not found (404). Marking as unposted in DB.")
                    try:
                        # Mark as posted=false so we don't keep checking it
                        supabase_patch(f"pins?id=eq.{pin['id']}", {"posted": False})
                    except Exception as e:
                        print(f"  Failed to update pin {pin['id']}: {e}")
                else:
                    print(f"  Could not fetch metrics for pin {pid}: {r.status_code}")
                time.sleep(0.3)
            except Exception as e:
                print(f"  Error fetching pin {pid}: {e}")

        print(f"  Pinterest saves: {total_saves} across {valid_pins} live pins ({len(pins)} total)")
        return total_saves, valid_pins

    except Exception as e:
        print(f"  Pinterest saves error: {e}")
        return 0, 0


# ── 4.5. Keyword research ─ Google autocomplete + Gemini ─────

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
        print(f"  Google autocomplete failed for '{query}': {e}")
        return []

def research_keywords(product):
    name     = product["name"]
    niche    = product.get("niche", "")
    category = product["category"]
    audience = product.get("audience", "")

    # Build seed queries
    seeds = [
        f"{niche} ideas pinterest",
        f"best {category} {niche}",
        f"{niche} aesthetic",
        f"{category} setup ideas",
        f"{niche} for {audience.split(',')[0].strip() if audience else 'everyone'}",
        f"{category} must haves",
    ]

    print(f"  Researching Pinterest keywords (US + UK)...")
    all_suggestions = []
    for seed in seeds:
        suggestions_uk = google_autocomplete(seed, country="uk")
        suggestions_us = google_autocomplete(seed, country="us")
        all_suggestions.extend(suggestions_uk + suggestions_us)
        time.sleep(0.3)

    unique = list(dict.fromkeys(all_suggestions))[:60]

    prompt = f"""You are a Pinterest SEO keyword expert.

Product: {name}
Niche: {niche} | Category: {category} | Audience: {audience}

Real Google autocomplete suggestions:
{json.dumps(unique, indent=2)}

Generate a refined list of 15-25 Pinterest-optimized keyword phrases.
Return ONLY a JSON array of strings, no markdown.
"""
    time.sleep(4) # Respect Gemini free tier rate limit
    for attempt in range(3):
        try:
            resp = requests.post(
                GEMINI_URL,
                json={"contents": [{"parts": [{"text": prompt}]}]}
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = text.replace("```json", "").replace("```", "").strip()
            keywords = json.loads(text)
            print(f"  Found {len(keywords)} Pinterest keywords")
            return keywords
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                print(f"  Gemini rate limit hit. Retrying in 10s... (Attempt {attempt + 1}/3)")
                time.sleep(10)
            else:
                break
        except Exception as e:
            break
            
    print(f"  Falling back to raw unique suggestions...")
    return unique[:20]


# ── 5. Deterministic scoring engine ──────────────────────────

def calculate_score(product, sig):
    """
    Deterministic, auditable score 0-100. No LLM needed.
    
    Weights:
      Pinterest Performance  40%  (CTR, engagement, volume)
      Revenue Potential      25%  (price × commission)
      Market Demand          20%  (BSR + Google Trends)
      Momentum               15%  (trend direction + save delta)
    """
    score = 0
    breakdown = []

    # ── 1. Pinterest Performance (40%) ── THE MONEY SIGNAL ──
    impressions = product.get("total_impressions", 0) or 0
    clicks      = product.get("total_clicks", 0) or 0
    saves       = sig.get("saves", 0)

    if impressions > 0:
        ctr = clicks / impressions
        engagement = (clicks + saves) / impressions

        # CTR scoring (20 pts)
        if   ctr >= 0.05: ctr_pts = 20
        elif ctr >= 0.02: ctr_pts = 15
        elif ctr >= 0.01: ctr_pts = 10
        elif ctr > 0:     ctr_pts = 5
        else:             ctr_pts = 0
        score += ctr_pts
        breakdown.append(f"CTR {ctr:.1%}={ctr_pts}pts")

        # Engagement scoring (10 pts)
        if   engagement >= 0.08: eng_pts = 10
        elif engagement >= 0.03: eng_pts = 7
        elif engagement >= 0.01: eng_pts = 4
        else:                    eng_pts = 1
        score += eng_pts

        # Volume bonus (10 pts) — more data = more confidence
        if   impressions >= 1000: vol_pts = 10
        elif impressions >= 500:  vol_pts = 7
        elif impressions >= 100:  vol_pts = 4
        else:                     vol_pts = 2
        score += vol_pts
        breakdown.append(f"{impressions} imp, {clicks} clicks")
    else:
        # New product — give it a fair chance to prove itself
        score += 15
        breakdown.append("new product bonus")

    # ── 2. Revenue Potential (25%) ───────────────────────────
    price      = product.get("price", 0) or 0
    commission = sig.get("commission", 3.0)
    payout     = price * (commission / 100)

    if   payout >= 5.0: rev_pts = 25
    elif payout >= 2.0: rev_pts = 18
    elif payout >= 1.0: rev_pts = 12
    elif payout >= 0.5: rev_pts = 6
    else:               rev_pts = 2
    score += rev_pts
    breakdown.append(f"£{payout:.2f} payout={rev_pts}pts")

    # ── 3. Market Demand (20%) ───────────────────────────────
    bsr = sig.get("bsr")
    if   bsr and bsr < 1000:    bsr_pts = 10
    elif bsr and bsr < 10000:   bsr_pts = 8
    elif bsr and bsr < 50000:   bsr_pts = 5
    elif bsr and bsr < 100000:  bsr_pts = 3
    else:                       bsr_pts = 4  # Unknown = average
    score += bsr_pts

    trend = sig.get("trend_score", 50) or 50
    if   trend >= 70: trend_pts = 10
    elif trend >= 40: trend_pts = 7
    elif trend >= 20: trend_pts = 4
    else:             trend_pts = 1
    score += trend_pts
    breakdown.append(f"BSR={bsr or '?'}, trend={trend}")

    # ── 4. Momentum (15%) ────────────────────────────────────
    trend_dir  = sig.get("trend_dir", "stable")
    save_delta = sig.get("save_delta", 0)

    if   trend_dir == "rising":  dir_pts = 8
    elif trend_dir == "stable":  dir_pts = 4
    else:                        dir_pts = 0
    score += dir_pts

    if   save_delta > 5:  mom_pts = 7
    elif save_delta > 0:  mom_pts = 5
    elif save_delta == 0: mom_pts = 2
    else:                 mom_pts = 0
    score += mom_pts

    # ── 5. Seasonal Bonus (up to 15 pts) ─────────────────────
    current_month = datetime.now(timezone.utc).month
    
    # Summer (May-August) keywords
    summer_keywords = ["summer", "fan", "cooling", "outdoor", "garden", "travel", "beach", "pool", "picnic", "bbq", "portable ac", "ice", "sun"]
    
    is_summer_season = 5 <= current_month <= 8
    
    seasonal_pts = 0
    if is_summer_season:
        name_lower = product.get("name", "").lower()
        niche_lower = sig.get("niche", "").lower()
        keywords = sig.get("keywords", [])
        
        has_seasonal_match = any(k in name_lower or k in niche_lower for k in summer_keywords)
        if not has_seasonal_match and keywords:
            has_seasonal_match = any(any(sk in kw.lower() for sk in summer_keywords) for kw in keywords)
            
        if has_seasonal_match:
            seasonal_pts = 15
            breakdown.append(f"seasonal boost (+{seasonal_pts}pts)")
            
    score += seasonal_pts

    final = min(score, 100)
    reason = " | ".join(breakdown)
    return final, reason


def score_all_products(products_data, signals):
    """Score all products deterministically. Returns list of {asin, score, reason}."""
    results = []
    for pd in products_data:
        asin = pd["asin"]
        sig  = signals.get(asin, {})
        # Merge product-level metrics into sig for the formula
        product_with_metrics = {**pd, **sig}
        score, reason = calculate_score(product_with_metrics, sig)
        results.append({"asin": asin, "score": score, "reason": reason})
    return results


# ── 6. Save scores to Supabase ───────────────────────────────

def save_score(product_id, score, reason, bsr, trend_score, trend_dir, trend_delta, saves, save_delta, keywords, active=None, pause_reason=None):
    payload = {
        "score":                    score,
        "score_reason":             reason,
        "bsr_rank":                 bsr,
        "trend_score":              trend_score,
        "trend_dir":                trend_dir,
        "trend_delta":              trend_delta,
        "pinterest_saves":          saves,
        "save_delta":               save_delta,
        "pinterest_keywords":       keywords,
        "keywords_last_updated_at": datetime.now(timezone.utc).isoformat(),
        "last_scored_at":           datetime.now(timezone.utc).isoformat()
    }
    if active is not None:
        payload["active"] = active
    if pause_reason:
        payload["pause_reason"] = pause_reason
    elif active:  # If re-activated, clear the pause reason
        payload["pause_reason"] = None

    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}",
        headers=SUPABASE_HEADERS,
        json=payload
    )
    resp.raise_for_status()


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Product scoring — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    products = load_products()
    if not products:
        print("No active products. Add products to the table first.")
        exit(0)

    # Single shared auth instance — token refreshed once if needed,
    # reused across all Pinterest API calls in this run
    auth = PinterestAuth()

    products_data = []
    signals       = {}

    # Create a persistent session for all Amazon requests
    amazon_session = requests.Session()
    amazon_session.headers.update(random_headers())
    try:
        print("Warming up Amazon session...")
        amazon_session.get("https://www.amazon.co.uk/", timeout=15)
        time.sleep(2)
    except: pass

    total_products = len(products)
    for i, p in enumerate(products):
        print(f"\n── [{i+1}/{total_products}] {p['name']} ({p['asin']}) ──")

        # ── 1. Smart Cooldown Check ──────────────────────────
        last_scored = p.get("last_scored_at")
        force_run = os.environ.get("FORCE_SCORE") == "1"
        
        if last_scored and not force_run:
            try:
                ls_dt = datetime.fromisoformat(last_scored.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - ls_dt).days
                if days_since < 6: # Only score once a week
                    print(f"  Skipping: Already scored {days_since} days ago.")
                    continue
            except Exception: pass

        # ── 2. Signal Fetching ───────────────────────────────
        amazon_sig = get_amazon_signals(p["asin"], session=amazon_session)
        bsr        = amazon_sig["bsr"]
        price      = amazon_sig["price"] or p.get("price", 0)
        available  = amazon_sig["available"]
        
        # ── Amazon Auto-Pause Logic ──────────────────────────
        active_status = True
        pause_reason  = None
        if not available:
            print("  ⚠️ AUTO-PAUSED: Currently Unavailable")
            active_status = False
            pause_reason  = "Product unavailable on Amazon"
        elif price > 0 and price < 10.0:
            print(f"  ⚠️ AUTO-PAUSED: Price too low (£{price})")
            active_status = False
            pause_reason  = f"Price too low (£{price})"

        # IMPROVEMENT: Use the Pinterest keyword for Trends
        trend_query = p["name"]
        if p.get("pinterest_keywords") and len(p["pinterest_keywords"]) > 0:
            trend_query = p["pinterest_keywords"][0]
            
        trend_score, trend_dir = get_trend_score(trend_query)
        saves, pin_count       = get_pinterest_saves(p["id"], auth)
        
        # Calculate Deltas (Momentum)
        prev_saves = p.get("pinterest_saves") or 0
        save_delta = saves - prev_saves if prev_saves > 0 else 0
        
        prev_trend = p.get("trend_score") or 0
        trend_delta = trend_score - prev_trend if prev_trend > 0 else 0

        # ── Pinterest Performance Auto-Pause ─────────────────
        # Use the real metrics from sync_metrics.py (already on products table)
        total_imp   = p.get("total_impressions", 0) or 0
        total_clk   = p.get("total_clicks", 0) or 0
        total_sav   = p.get("total_saves", 0) or 0

        # Find the earliest pin date for a fair trial period
        if active_status and pin_count > 0:
            try:
                earliest_pin = supabase_get("pins", params={
                    "product_id": f"eq.{p['id']}",
                    "posted": "eq.true",
                    "select": "created_at",
                    "order": "created_at.asc",
                    "limit": "1"
                })
                if earliest_pin:
                    fp_dt = datetime.fromisoformat(
                        earliest_pin[0]["created_at"].replace("Z", "+00:00")
                    )
                    days_since_first_pin = (datetime.now(timezone.utc) - fp_dt).days
                else:
                    days_since_first_pin = 0
            except Exception:
                days_since_first_pin = 0

            # Primary signal: clicks (the money funnel)
            if pin_count >= 3 and days_since_first_pin >= 7 and total_clk == 0 and total_sav == 0:
                print(f"  ⚠️ AUTO-PAUSED: Zero clicks & saves after {pin_count} pins over {days_since_first_pin}d")
                active_status = False
                pause_reason  = f"Zero engagement ({pin_count} pins, {days_since_first_pin}d, {total_imp} imp)"
            elif trend_dir == "falling" and total_clk == 0 and pin_count >= 2:
                print(f"  ⚠️ AUTO-PAUSED: Falling trend + zero clicks")
                active_status = False
                pause_reason  = "Falling trend with no click-through"

        # ── 3. Keyword Research (if needed) ──────────────────
        keywords     = p.get("pinterest_keywords")
        last_updated = p.get("keywords_last_updated_at")
        
        needs_refresh = True
        if keywords and last_updated:
            try:
                lu_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                days_old = (datetime.now(timezone.utc) - lu_dt).days
                if days_old < 30:
                    needs_refresh = False
            except Exception: pass

        if needs_refresh:
            keywords = research_keywords(p)
            try:
                supabase_patch(f"products?id=eq.{p['id']}", {
                    "pinterest_keywords":       keywords,
                    "keywords_last_updated_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as e:
                print(f"  Warning: failed to save keywords: {e}")

        # ── 4. Final Signal Package ──────────────────────────
        commission = p.get("commission") or COMMISSION_RATES.get(
            p.get("category", "").lower(),
            COMMISSION_RATES["default"]
        )

        signals[p["asin"]] = {
            "id":              p["id"],
            "bsr":             bsr,
            "trend_score":     trend_score,
            "trend_dir":       trend_dir,
            "trend_delta":     trend_delta,
            "saves":           saves,
            "save_delta":      save_delta,
            "pin_count":       pin_count,
            "commission":      commission,
            "price":           p.get("price", 0),
            "category":        p.get("category", "unknown"),
            "keywords":        keywords,
            "active_status":   active_status,
            "pause_reason":    pause_reason
        }

        products_data.append({
            "asin":              p["asin"],
            "name":              p["name"],
            "category":          p["category"],
            "price":             p.get("price", 0),
            "total_impressions": total_imp,
            "total_clicks":      total_clk,
            "total_saves":       total_sav,
            "bsr_rank":          bsr,
            "trend_score":       trend_score,
            "trend_dir":         trend_dir,
            "saves":             saves,
            "pin_count":         pin_count,
            "commission":        commission,
            "keywords":          keywords
        })

        # Wait longer between products to reduce pressure on Amazon/Google
        time.sleep(random.uniform(5, 8))

    # Deterministic scoring — no LLM needed
    print(f"\nScoring {len(products_data)} products (deterministic formula)...")
    scores = score_all_products(products_data, signals)

    # Save to Supabase
    print("\nSaving scores...")
    for s in scores:
        asin = s["asin"]
        sig  = signals.get(asin, {})
        is_paused = not sig.get("active_status", True)
        save_score(
            product_id   = sig["id"],
            score        = s["score"],
            reason       = s["reason"],
            bsr          = sig.get("bsr"),
            trend_score  = sig.get("trend_score", 0),
            trend_dir    = sig.get("trend_dir", "stable"),
            trend_delta  = sig.get("trend_delta", 0),
            saves        = sig.get("saves", 0),
            save_delta   = sig.get("save_delta", 0),
            keywords     = sig.get("keywords", []),
            active       = sig.get("active_status"),
            pause_reason = sig.get("pause_reason")
        )
        status = "⏸️ PAUSED" if is_paused else "✅"
        print(f"  {status} {asin} → {s['score']}/100 — {s['reason']}")
        if is_paused:
            print(f"     Reason: {sig.get('pause_reason')}")

    top         = max(scores, key=lambda x: x["score"])
    top_product = next(p for p in products if p["asin"] == top["asin"])

    print(f"\n{'='*60}")
    print(f"TOP PRODUCT THIS WEEK: {top_product['name']}")
    print(f"Score: {top['score']}/100 — {top['reason']}")
    print(f"{'='*60}")
    print("Done. generate_pins.py will promote this product next run.")