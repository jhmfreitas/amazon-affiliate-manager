"""
score_products.py
─────────────────
Runs weekly (Monday 7am UTC) via GitHub Actions.

For each active product:
  1. Fetches Amazon BSR via scraping (no API needed)
  2. Fetches Google Trends interest score via pytrends
  3. Reads Pinterest save counts from your own pin analytics
  4. Asks Gemini to score each product 1-100
  5. Saves scores back to Supabase

Top-scored product is then picked by generate_pins.py.
"""

import os, json, time, re, requests, random
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from pinterest_auth import PinterestAuth
from config import (
    log, random_headers, get_amazon_cookies, 
    supabase_get, supabase_patch, SUPABASE_HEADERS, SUPABASE_URL
)

# ── Secrets ──────────────────────────────────────────────────
GEMINI_KEY   = os.environ["GEMINI_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta"
    f"/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
)

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

def get_amazon_bsr(asin):
    """Fetch BSR rank directly from Amazon product page with retries."""
    url = f"https://www.amazon.co.uk/dp/{asin}"
    for attempt in range(1, 4):
        try:
            headers = random_headers()
            headers["Referer"] = "https://www.amazon.co.uk/"
            cookies = get_amazon_cookies()
            
            resp = requests.get(url, headers=headers, cookies=cookies, timeout=15)
            
            if resp.status_code == 200:
                if "api-services-support@amazon.com" in resp.text or "To discuss automated access" in resp.text:
                    if attempt < 3:
                        wait = random.uniform(5, 10)
                        print(f"  BSR: Attempt {attempt} blocked (CAPTCHA) for {asin}. Retrying in {wait:.1f}s...")
                        time.sleep(wait)
                        continue
                    else:
                        print(f"  BSR: Final attempt blocked (CAPTCHA) for {asin}")
                        return None
                
                soup = BeautifulSoup(resp.text, "html.parser")
                
                # Method 1: Search for "Best Sellers Rank" label (handles nested tags)
                # re.S allows . to match newlines if needed, though not used here
                bsr_label = soup.find(string=re.compile(r"Best\s*Sellers?\s*Rank", re.I))
                if bsr_label:
                    container = bsr_label.find_parent(["span", "li", "td", "div"])
                    if container:
                        bsr_text = container.get_text(strip=True)
                        match = re.search(r"#([\d,]+)\s+in\s+", bsr_text)
                        if match:
                            rank = int(match.group(1).replace(",", ""))
                            print(f"  BSR: #{rank:,}")
                            return rank

                # Method 2: Ultra-loose search for ANY element containing "#123 in Category"
                # This catches cases where the label is missing or obscured
                potential_tags = soup.find_all(["span", "li", "td", "b"], string=re.compile(r"#[\d,]+\s+in\s+", re.I))
                for tag in potential_tags:
                    txt = tag.get_text(strip=True)
                    match = re.search(r"#([\d,]+)\s+in\s+([\w\s&]+)", txt)
                    if match:
                        rank = int(match.group(1).replace(",", ""))
                        print(f"  BSR: #{rank:,} (found via loose match)")
                        return rank

                # Method 3: Global fallback regex (most desperate)
                match = re.search(r"Best\s*Sellers?\s*Rank:?\s*#([\d,]+)\s+in\s+([\w\s&]+)", resp.text, re.I)
                if match:
                    rank = int(match.group(1).replace(",", ""))
                    print(f"  BSR: #{rank:,} (found via global regex)")
                    return rank
                
                # Debug info if not found
                has_captcha = "api-services-support@amazon.com" in resp.text
                print(f"  BSR: not found (Length: {len(resp.text)}, Captcha: {has_captcha})")
                return None
            
            elif resp.status_code == 404:
                print(f"  BSR: 404 Not Found for {asin}")
                return None
            else:
                print(f"  BSR: Attempt {attempt} failed for {asin}: Status {resp.status_code}")
                time.sleep(2)

        except Exception as e:
            print(f"  BSR: Attempt {attempt} error for {asin}: {e}")
            time.sleep(2)
            
    return None


# ── 3. Google Trends ─────────────────────────────────────────

def get_trend_score(product_name):
    """Fetch interest score from Google Trends via pytrends."""
    import random
    for attempt in range(1, 3):
        try:
            from pytrends.request import TrendReq
            pt = TrendReq(hl="en-GB", tz=0, timeout=(15, 30))
            keyword = " ".join(product_name.split()[:3])
            
            # If second attempt, wait longer
            if attempt > 1:
                time.sleep(random.uniform(10, 20))
            else:
                time.sleep(random.uniform(2, 5))
            
            print(f"  Trends: Researching keyword: '{keyword}' (Attempt {attempt})")
            pt.build_payload([keyword], timeframe="now 7-d", geo="GB")
            data = pt.interest_over_time()

            if data.empty:
                print(f"  Trends: no data for '{keyword}'")
                return 50, "stable"

            values = data[keyword].tolist()
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
                print(f"  Trends: 429 Rate Limit hit — using default")
            else:
                print(f"  Trends error: {e}")
            return 50, "stable"
    return 50, "stable"


# ── 4. Pinterest pin saves for this product ──────────────────

def get_pinterest_saves(product_id, auth: PinterestAuth):
    """
    Sum saves across all posted pins for this product.
    Uses shared PinterestAuth — auto-refreshes token on 401.
    """
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/pins",
            headers=SUPABASE_HEADERS,
            params={
                "product_id": f"eq.{product_id}",
                "posted":     "eq.true",
                "select":     "pinterest_id"
            }
        )
        resp.raise_for_status()
        pins = resp.json()

        if not pins:
            print(f"  Pinterest saves: no posted pins yet")
            return 0

        total_saves = 0
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
                else:
                    print(f"  Could not fetch metrics for pin {pid}: {r.status_code}")
                time.sleep(0.3)
            except Exception as e:
                print(f"  Error fetching pin {pid}: {e}")

        print(f"  Pinterest saves: {total_saves} across {len(pins)} pins")
        return total_saves

    except Exception as e:
        print(f"  Pinterest saves error: {e}")
        return 0


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


# ── 5. Gemini scoring engine ─────────────────────────────────

def score_products_with_gemini(products_data):
    prompt = f"""You are an Amazon affiliate marketing analyst.
Score each product 0-100 for Pinterest promotion potential THIS WEEK.

Scoring weights:
- Pinterest Search Intent 35% (how strong/relevant are the discovered keywords?)
- Amazon BSR rank         20% (lower = more popular = higher score)
- Google Trends score     15% (higher interest = higher score)
- Trend direction         10% (rising=+10, stable=+0, falling=-10)
- Pinterest saves         10% (more saves = better conversion proof)
- Commission rate         10% (higher % = higher score)

BSR scoring guide:
- Under 1,000    → 20 points
- 1,000-10,000   → 15 points
- 10,000-50,000  → 10 points
- 50,000-100,000 → 5 points
- Over 100,000   → 2 points
- Unknown        → 8 points (assume average)

Products to score (including their discovered Pinterest keywords):
{json.dumps(products_data, indent=2)}

Return ONLY valid JSON array, no markdown:
[
  {{
    "asin":   "...",
    "score":  87.5,
    "reason": "one sentence explaining the score based heavily on keyword intent"
  }}
]"""

    resp = requests.post(
        GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


# ── 6. Save scores to Supabase ───────────────────────────────

def save_score(product_id, score, reason, bsr, trend_score, trend_dir, saves, keywords):
    resp = requests.patch(
        f"{SUPABASE_URL}/rest/v1/products?id=eq.{product_id}",
        headers=SUPABASE_HEADERS,
        json={
            "score":                    score,
            "score_reason":             reason,
            "bsr_rank":                 bsr,
            "trend_score":              trend_score,
            "trend_dir":                trend_dir,
            "pinterest_saves":          saves,
            "pinterest_keywords":       keywords,
            "keywords_last_updated_at": datetime.now(timezone.utc).isoformat(),
            "last_scored_at":           datetime.now(timezone.utc).isoformat()
        }
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

    for p in products:
        print(f"\n── {p['name']} ({p['asin']}) ──")

        bsr                    = get_amazon_bsr(p["asin"])
        trend_score, trend_dir = get_trend_score(p["name"])
        saves                  = get_pinterest_saves(p["id"], auth)
        keywords               = p.get("pinterest_keywords")
        last_updated           = p.get("keywords_last_updated_at")
        
        needs_refresh = True
        if keywords and last_updated:
            try:
                # Check if keywords are older than 30 days
                lu_dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
                days_old = (datetime.now(timezone.utc) - lu_dt).days
                if days_old < 30:
                    needs_refresh = False
            except Exception:
                pass

        if needs_refresh:
            keywords = research_keywords(p)
            # Save keywords to DB for generate_pins to use
            try:
                supabase_patch(f"products?id=eq.{p['id']}", {
                    "pinterest_keywords":       keywords,
                    "keywords_last_updated_at": datetime.now(timezone.utc).isoformat()
                })
            except Exception as e:
                print(f"  Warning: failed to save keywords: {e}")
        commission             = p.get("commission") or COMMISSION_RATES.get(
                                   p.get("category", "").lower(),
                                   COMMISSION_RATES["default"]
                                 )

        signals[p["asin"]] = {
            "id":          p["id"],
            "bsr":         bsr,
            "trend_score": trend_score,
            "trend_dir":   trend_dir,
            "saves":       saves,
            "commission":  commission,
            "keywords":    keywords
        }

        products_data.append({
            "asin":        p["asin"],
            "name":        p["name"],
            "category":    p["category"],
            "bsr_rank":    bsr,
            "trend_score": trend_score,
            "trend_dir":   trend_dir,
            "saves":       saves,
            "commission":  commission,
            "keywords":    keywords
        })

        # Wait longer between products to reduce pressure on Amazon/Google
        time.sleep(random.uniform(5, 8))

    # Score with Gemini
    print(f"\nScoring {len(products_data)} products with Gemini...")
    scores = score_products_with_gemini(products_data)

    # Save to Supabase
    print("\nSaving scores...")
    for s in scores:
        asin = s["asin"]
        sig  = signals.get(asin, {})
        save_score(
            product_id  = sig["id"],
            score       = s["score"],
            reason      = s["reason"],
            bsr         = sig.get("bsr"),
            trend_score = sig.get("trend_score", 0),
            trend_dir   = sig.get("trend_dir", "stable"),
            saves       = sig.get("saves", 0),
            keywords    = sig.get("keywords", [])
        )
        print(f"  {asin} → {s['score']}/100 — {s['reason']}")

    top         = max(scores, key=lambda x: x["score"])
    top_product = next(p for p in products if p["asin"] == top["asin"])

    print(f"\n{'='*60}")
    print(f"TOP PRODUCT THIS WEEK: {top_product['name']}")
    print(f"Score: {top['score']}/100 — {top['reason']}")
    print(f"{'='*60}")
    print("Done. generate_pins.py will promote this product next run.")