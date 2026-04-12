import os, json, random, requests, uuid

GEMINI_KEY    = os.environ["GEMINI_API_KEY"]
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]   # service_role key
PEXELS_KEY    = os.environ["PEXELS_API_KEY"]

GEMINI_MODEL  = "gemini-2.5-flash-lite"
GEMINI_URL    = (
    f"https://generativelanguage.googleapis.com/v1beta"
    f"/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
)

# ── Config — edit these for your niche ───────────────────────
NICHE    = "home office"
PRODUCT  = "standing desk"
AUDIENCE = "remote workers"
SLOTS    = 7    # pins to fill this week
CANDIDATES_PER_SLOT = 10  # generate 10, keep top 3 per slot
KEEP_TOP = 3
# ─────────────────────────────────────────────────────────────

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}


def gemini(prompt):
    resp = requests.post(GEMINI_URL,
        json={"contents": [{"parts": [{"text": prompt}]}]})
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return text.replace("```json","").replace("```","").strip()


def generate_candidates(n):
    prompt = f"""You are a Pinterest affiliate expert.
Generate {n} UNIQUE Pinterest pin candidates for this product.

Niche: {NICHE}
Product: {PRODUCT}
Audience: {AUDIENCE}

Return ONLY a valid JSON array:
[{{
  "title": "max 100 chars, pain-point hook, keyword-rich",
  "description": "150-200 words, first-person, conversational, soft CTA at end",
  "hashtags": ["12 tags, no # prefix, mix broad and niche"],
  "pexels_search": "2-4 word lifestyle scene, NOT the product name",
  "hook_type": "one of: pain_point | curiosity | social_proof | listicle | urgency"
}}]

Each pin must use a DIFFERENT hook_type and angle. No repetition."""
    return json.loads(gemini(prompt))


def score_candidates(candidates):
    prompt = f"""You are a Pinterest conversion expert.
Score each pin candidate 1-10 on conversion potential.

Criteria:
- Hook strength (stops the scroll)
- Keyword density (Pinterest SEO)
- Emotional resonance
- CTA clarity

Candidates:
{json.dumps(candidates, indent=2)}

Return ONLY a JSON array of scores in the same order:
[{{"score": 8.5, "reason": "strong pain point hook"}}, ...]"""
    scores = json.loads(gemini(prompt))
    for i, c in enumerate(candidates):
        c["score"] = scores[i]["score"]
        c["score_reason"] = scores[i]["reason"]
    return sorted(candidates, key=lambda x: x["score"], reverse=True)


def get_pexels_image(query):
    resp = requests.get(
        "https://api.pexels.com/v1/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": query, "per_page": 15,
                "orientation": "portrait", "size": "large"}
    )
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        return None, None
    photo = random.choice(photos)
    return photo["src"]["large2x"], photo["photographer"]


def upload_image_to_supabase(image_url):
    """Download from Pexels, upload to Supabase Storage, return public URL."""
    img_data = requests.get(image_url, timeout=15).content
    file_name = f"{uuid.uuid4()}.jpg"
    bucket = "pin-images"

    upload_resp = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{bucket}/{file_name}",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "image/jpeg",
        },
        data=img_data
    )
    upload_resp.raise_for_status()
    public_url = (
        f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{file_name}"
    )
    return public_url


def save_pin_to_supabase(pin):
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=SUPABASE_HEADERS,
        json={
            "title":        pin["title"],
            "description":  pin["description"],
            "hashtags":     pin["hashtags"],
            "pexels_search": pin["pexels_search"],
            "hook_type":    pin["hook_type"],
            "score":        pin["score"],
            "score_reason": pin["score_reason"],
            "image_url":    pin["image_url"],
            "photographer": pin.get("photographer", ""),
            "approved":     False,
            "posted":       False,
        }
    )
    resp.raise_for_status()
    return resp.json()[0]


if __name__ == "__main__":
    all_saved = []

    for slot in range(SLOTS):
        print(f"\nSlot {slot + 1}/{SLOTS}")

        print(f"  Generating {CANDIDATES_PER_SLOT} candidates...")
        candidates = generate_candidates(CANDIDATES_PER_SLOT)

        print(f"  Scoring candidates...")
        ranked = score_candidates(candidates)
        top = ranked[:KEEP_TOP]

        for i, pin in enumerate(top):
            print(f"  Saving candidate {i+1} (score {pin['score']})...")
            pexels_url, photographer = get_pexels_image(pin["pexels_search"])
            if not pexels_url:
                print(f"  No image found for '{pin['pexels_search']}', skipping.")
                continue

            print(f"  Uploading image to Supabase Storage...")
            pin["image_url"] = upload_image_to_supabase(pexels_url)
            pin["photographer"] = photographer

            saved = save_pin_to_supabase(pin)
            all_saved.append(saved)
            print(f"  Saved pin id={saved['id']}: {pin['title'][:60]}...")

    print(f"\nDone. {len(all_saved)} pins saved. Go approve them in your review app.")