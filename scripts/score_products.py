"""
score_products_v2.py
Deterministic scoring engine (no LLM)

Focus:
- Demand (Google Trends)
- Momentum (delta signals)
- Proof (Pinterest saves)
- Monetization (commission)
- Weak signal (BSR fallback)
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

    stopwords = {
        "pack","set","bundle","with","logo","waistband",
        "for","and","the","a","of","mens","men","womens","women"
    }

    words = [w for w in re.split(r"\W+", name) if w and w not in stopwords]

    if len(words) < 2:
        return [product_name]

    brand = " ".join(words[:2])
    product = words[-1]

    return list(set([
        f"{brand} {product}",
        brand,
        product
    ]))

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
    return max(-10, min(10, delta / scale * 10))

def normalize_saves(saves):
    return min(100, math.log1p(saves) * 15)

# ── SCORING ENGINE ─────────────────────────────────
def compute_score(p):
    trend = p["trend_score"]
    saves = p["pinterest_saves"]

    trend_delta = normalize_delta(p["trend_delta"])
    save_delta  = normalize_delta(p["save_delta"])

    direction_bonus = {
        "rising": 10,
        "stable": 0,
        "falling": -10
    }

    # Base score driven by demand and proof
    base = (trend * 0.45) + (saves * 0.35)

    # Momentum bonus/penalty (can be negative)
    momentum = (
        direction_bonus.get(p["trend_dir"], 0) +
        (trend_delta * 0.10) +
        (save_delta * 0.10)
    )

    # Commission bonus (higher commission = more points)
    # e.g., 6% commission = +18 points, 3% = +9 points
    comm_bonus = p.get("commission", 3.0) * 3.0

    score = base + momentum + comm_bonus

    # Prevent negative scores
    return round(max(0.0, score), 2)

# ── SAVE ───────────────────────────────────────────
def save_score(product_id, data):
    supabase_patch(f"products?id=eq.{product_id}", data)

# ── MAIN ───────────────────────────────────────────
if __name__ == "__main__":
    log.info("=== SCORING ENGINE V2 ===")

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