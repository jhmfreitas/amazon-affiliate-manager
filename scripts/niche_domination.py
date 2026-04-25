import os, re, random, time, requests
from collections import defaultdict

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ── LOAD SCORED PRODUCTS ───────────────────────────
def load_products():
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/products",
        headers=HEADERS,
        params={"active": "eq.true", "select": "*"}
    )
    return r.json()

# ── NICHE EXTRACTION ───────────────────────────────
def extract_niche(name):
    stopwords = {
        "pack","set","bundle","with","logo","waistband",
        "for","and","the","a","of"
    }

    words = [w for w in re.split(r"\W+", name.lower()) if w and w not in stopwords]

    if len(words) < 2:
        return name.lower()

    return " ".join(words[:2] + [words[-1]])

# ── GROUP INTO NICHES ─────────────────────────────
def group_niches(products):
    niches = defaultdict(list)

    for p in products:
        niche = extract_niche(p["name"])
        niches[niche].append(p)

    return niches

# ── SCORE NICHE ───────────────────────────────────
def score_niche(products):
    avg_score = sum(p.get("score", 0) for p in products) / len(products)
    total_saves = sum(p.get("pinterest_saves", 0) for p in products)
    trend = max(p.get("trend_score", 0) for p in products)

    return avg_score + trend * 0.3 + total_saves * 0.01

# ── PIN CONTENT GENERATOR ─────────────────────────
ANGLES = [
    "problem",
    "list",
    "social",
    "curiosity",
    "comparison"
]

def generate_title(niche, angle):
    templates = {
        "problem": f"Stop wasting money on {niche} (try this)",
        "list": f"Top 5 {niche} everyone is buying",
        "social": f"Why everyone is switching to {niche}",
        "curiosity": f"This {niche} is blowing up right now",
        "comparison": f"{niche}: cheap vs premium (big difference)"
    }
    return templates.get(angle, niche)

def generate_description(niche, angle):
    return f"Discover the best {niche} trending this week. Limited stock."

def generate_variations(niche):
    variations = []

    for angle in ANGLES:
        variations.append({
            "niche": niche,
            "angle": angle,
            "title": generate_title(niche, angle),
            "description": generate_description(niche, angle)
        })

    return variations

# ── SAVE PINS TO DB ───────────────────────────────
def save_pin(product_id, data):
    payload = {
        "product_id": product_id,
        "title": data["title"],
        "description": data["description"],
        "angle": data["angle"],
        "posted": False
    }

    requests.post(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=HEADERS,
        json=payload
    )

# ── MAIN ─────────────────────────────────────────
if __name__ == "__main__":
    print("=== NICHE DOMINATION ===")

    products = load_products()
    niches = group_niches(products)

    ranked = sorted(
        niches.items(),
        key=lambda x: score_niche(x[1]),
        reverse=True
    )

    top_niches = ranked[:3]

    for niche, prods in top_niches:
        print(f"\n🔥 Niche: {niche}")

        variations = generate_variations(niche)

        # pick best product in that niche
        best_product = max(prods, key=lambda x: x.get("score", 0))

        for v in variations:
            save_pin(best_product["id"], v)
            print(f"  + {v['title']}")
            time.sleep(0.2)

    print("\nDone.")