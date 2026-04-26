"""
score_products_v3.py
Deterministic scoring engine (no LLM)

Focus:
- Demand (Google Trends)
- Momentum (delta signals)
- Proof (Pinterest saves)
- Monetization (commission)
"""

import re, time, math, requests
from datetime import datetime, timezone
from pinterest_auth import PinterestAuth
from config import log, supabase_get, supabase_patch, random_headers, get_amazon_cookies

# ── LOAD PRODUCTS ───────────────────────────────────
def load_products():
    return supabase_get("products", params={
        "active": "eq.true",
        "order": "id.asc"
    })

# ── GOOGLE TRENDS KEYWORD ENGINE ────────────────────
def extract_trend_candidates(product_name):
    name = product_name.lower()
    stopwords = {"pack","set","bundle","with","for","and","the","a","of","mens","men","womens","women","in"}
    
    # Remove everything after a pipe, dash, or comma which usually denotes fluff
    clean_name = re.split(r'[,|\-]', name)[0]
    words = [w for w in re.split(r"\W+", clean_name) if w and w not in stopwords]
    
    if len(words) == 0:
        return [product_name[:30]]
    if len(words) == 1:
        return [words[0]]
        
    candidates = []
    # e.g., "Under Armour Charged"
    if len(words) >= 3:
        candidates.append(" ".join(words[:3]))
    # e.g., "Under Armour"
    if len(words) >= 2:
        candidates.append(" ".join(words[:2]))
        
    return candidates

def get_trend_score(product_name):
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-GB", tz=0)

        queries = extract_trend_candidates(product_name)

        best_score = 0
        best_direction = "stable"

        for q in queries:
            pt.build_payload([q], timeframe="now 7-d", geo="GB")
            data = pt.interest_over_time()

            if data.empty:
                continue

            values = data[q].tolist()
            avg = sum(values) / len(values)

            mid = len(values) // 2
            first = sum(values[:mid]) / max(mid, 1)
            second = sum(values[mid:]) / max(len(values) - mid, 1)

            if second > first * 1.15:
                direction = "rising"
            elif second < first * 0.85:
                direction = "falling"
            else:
                direction = "stable"

            if avg > best_score:
                best_score = avg
                best_direction = direction

        if best_score == 0:
            return 40, "stable"

        return round(best_score, 1), best_direction

    except Exception as e:
        log.warning(f"Trends error: {e}")
        return 50, "stable"

# ── PINTEREST SAVES ────────────────────────────────
def get_pinterest_saves(product_id, auth):
    pins = supabase_get("pins", params={
        "product_id": f"eq.{product_id}",
        "posted": "eq.true"
    })

    total = 0
    for p in pins:
        pid = p.get("pinterest_id")
        if not pid:
            continue

        r = auth.get(f"https://api.pinterest.com/v5/pins/{pid}",
                     params={"pin_metrics": "true"})

        if r.ok:
            total += r.json().get("pin_metrics", {}) \
                             .get("lifetime_metrics", {}) \
                             .get("save", 0)

        time.sleep(0.2)

    return total

# ── NORMALIZATION ──────────────────────────────────
def normalize_delta(delta, scale=20):
    if delta is None:
        delta = 0.0
    delta = float(delta)
    return max(-10, min(10, delta / scale * 10))

def normalize_saves(saves):
    return min(100, math.log1p(saves) * 15)

# ── SCORING ENGINE ─────────────────────────────────
def compute_score(p):
    trend = float(p.get("trend_score") or 0.0)
    saves = int(p.get("pinterest_saves") or 0)

    trend_delta = normalize_delta(p.get("trend_delta"))
    save_delta  = normalize_delta(p.get("save_delta"))

    direction_bonus = {
        "rising": 10,
        "stable": 0,
        "falling": -10
    }

    # Base score driven by demand and proof
    base = (trend * 0.45) + (saves * 0.35)

    # Momentum bonus/penalty (can be negative)
    # Removing the *0.10 so delta points actually matter!
    momentum = (
        direction_bonus.get(p["trend_dir"], 0) +
        trend_delta +
        save_delta
    )

    # Commission bonus (Cash Payout £)
    # Expected payout = Price * Commission%
    raw_price = p.get("price")
    price = float(raw_price) if raw_price is not None else 15.0
    
    raw_comm = p.get("commission")
    commission_pct = float(raw_comm) if raw_comm is not None else 3.0
    
    payout = price * (commission_pct / 100.0)
    
    # Scale payout to a reasonable bonus (e.g. £1 payout = ~3 points, £10 = ~30 points)
    # Capped at 40 points so a £1000 item doesn't break the scale completely
    comm_bonus = min(40.0, payout * 3.0)

    # Freshness Bonus (Cold Start Fix)
    # Give new products +15 points to compete with older products that have saves
    freshness_bonus = 0
    if p.get("created_at"):
        try:
            created = datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - created).days <= 7:
                freshness_bonus = 15.0
        except Exception:
            pass

    score = base + momentum + comm_bonus + freshness_bonus

    # Ensure score stays strictly within a 0-100 scale
    return round(min(100.0, max(0.0, score)), 2)

# ── SAVE ───────────────────────────────────────────
def save_score(product_id, data):
    supabase_patch(f"products?id=eq.{product_id}", data)

# ── MAIN ───────────────────────────────────────────
if __name__ == "__main__":
    log.info("=== SCORING ENGINE V3 ===")

    products = load_products()
    auth = PinterestAuth()

    results = []

    for p in products:
        log.info(f"\n→ {p['name']}")

        trend_score, trend_dir = get_trend_score(p["name"])
        saves = get_pinterest_saves(p["id"], auth)

        prev_trend = p.get("trend_score") or trend_score
        prev_saves = p.get("pinterest_saves") or saves

        trend_delta = trend_score - prev_trend
        save_delta  = saves - prev_saves

        data = {
            "trend_score": trend_score,
            "trend_dir": trend_dir,
            "pinterest_saves": saves,
            "trend_delta": trend_delta,
            "save_delta": save_delta
        }

        score = compute_score({
            **data,
            "commission": p.get("commission", 3.0)
        })

        log.info(f"  Trend: {trend_score} ({trend_dir}) Δ{trend_delta}")
        log.info(f"  Saves: {saves} Δ{save_delta}")
        log.info(f"  Score: {score}")

        save_score(p["id"], {
            **data,
            "score": score,
            "last_scored_at": datetime.now(timezone.utc).isoformat()
        })

        results.append((p["name"], score))

        time.sleep(2)

    if results:
        top = max(results, key=lambda x: x[1])
        log.info("\n=== TOP PRODUCT ===")
        log.info(top)
    else:
        log.info("\nNo products scored.")