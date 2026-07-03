"""
repin_winners.py
────────────────
Runs weekly (Wednesdays) via GitHub Actions.

Finds products with the highest impressions but low clicks (opportunity gap),
and creates fresh pin variations to give the Pinterest algorithm another
chance to distribute the content.

Strategy: If a product is getting eyeballs (impressions) but not clicks,
the problem is the pin creative — not the product. A fresh angle can unlock
the conversion.
"""

import os
import sys
import time
import json
import random
import requests
from datetime import datetime, timezone
from config import (
    SUPABASE_URL, SUPABASE_HEADERS,
    supabase_get, supabase_post, log
)

# Reuse the pin generation machinery
from generate_pins import (
    generate_candidates, get_pexels_image, create_pin_image,
    upload_image, get_board_for_product, get_affiliate_url, load_font
)

# ── Config ───────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
MAX_REPINS     = 3   # Max new pin variations per run


def find_opportunity_products():
    """
    Find products where impressions are high but CTR is low.
    These are "opportunity gap" products — people see the pin but don't click.
    A fresh creative can unlock the conversion.
    """
    # Get all active products with their metrics
    products = supabase_get("products", params={
        "active": "eq.true",
        "order": "total_impressions.desc",
        "limit": "20"
    })
    
    opportunities = []
    for p in products:
        impressions = p.get("total_impressions", 0) or 0
        clicks = p.get("total_clicks", 0) or 0
        
        # Need at least 50 impressions to judge
        if impressions < 50:
            continue
        
        ctr = clicks / impressions if impressions > 0 else 0
        
        # Opportunity: high impressions but low CTR (< 1%)
        # OR: decent impressions but zero clicks
        if (impressions >= 100 and ctr < 0.01) or (impressions >= 50 and clicks == 0):
            # Check how many pins already exist for this product
            pins = supabase_get("pins", params={
                "product_id": f"eq.{p['id']}",
                "select": "id,title",
            })
            
            opportunities.append({
                "product": p,
                "impressions": impressions,
                "clicks": clicks,
                "ctr": ctr,
                "existing_pin_count": len(pins),
                "existing_titles": [pin.get("title", "") for pin in pins],
            })
    
    # Sort by impression volume (biggest opportunity first)
    opportunities.sort(key=lambda x: x["impressions"], reverse=True)
    return opportunities[:MAX_REPINS]


def generate_fresh_variation(product, existing_titles):
    """
    Generate a NEW pin with a DIFFERENT angle than existing pins.
    Pass existing titles to Gemini so it avoids repeating the same hooks.
    """
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    category = product.get('category', 'Fashion')
    price = product.get('price', 0)
    price_str = f"£{price:.0f}" if price else "affordable"
    keywords = product.get('pinterest_keywords', [])
    
    existing_str = "\n".join(f"- {t}" for t in existing_titles) if existing_titles else "(none yet)"
    
    prompt = f"""Create 1 FRESH Pinterest pin idea for this product. It MUST use a DIFFERENT angle
from the existing pins listed below.

PRODUCT: {product['name']}
CATEGORY: {category}
PRICE: {price_str}
KEYWORDS: {keywords}

EXISTING PIN TITLES (DO NOT repeat these angles):
{existing_str}

Use a completely different hook strategy. If existing pins use price, try benefit.
If they use first-person, try question format. If they use urgency, try social proof.

Hook strategies to choose from:
- Question: "Looking for the perfect summer bag?"
- Social proof: "Everyone's asking about this necklace"
- Benefit: "The dress that works for 5 different occasions"
- Curiosity: "I didn't expect this £22 top to look this expensive"
- Seasonal: "My holiday packing must-have this summer"

RULES:
1. Title: Under 60 characters, scroll-stopping hook
2. Description: 200-400 chars, front-load keywords, no hashtags, end with soft CTA
3. Alt text: Visual description, 100-150 chars

Return ONLY a JSON list with ONE object containing:
'title', 'description', 'alt_text', 'keywords' (list of 5-8 phrases), 'pexels_search' (2-3 words)
"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9}  # Higher temp for more creative variation
    }
    
    for attempt in range(3):
        try:
            resp = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data['candidates'][0]['content']['parts'][0]['text']
            
            raw_text = raw_text.strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            if raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            
            candidates = json.loads(raw_text.strip())
            if isinstance(candidates, list) and len(candidates) > 0:
                return candidates[0]
            return None
            
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                err_msg = e.response.text
            print(f"  Gemini Error (Attempt {attempt+1}/3): {err_msg}")
            
            if attempt < 2:
                if "429" in err_msg or "503" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    print("  Switching to gemini-2.0-flash for next attempt...")
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
                time.sleep(5 * (attempt + 1))
    
    return None


# ── Main ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print(f"Repin Winners — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    has_errors = False
    opportunities = find_opportunity_products()
    
    if not opportunities:
        print("No opportunity-gap products found yet.")
        print("Need products with 50+ impressions before we can identify winners.")
        sys.exit(0)
    
    print(f"Found {len(opportunities)} opportunity-gap products:\n")
    
    created = 0
    for opp in opportunities:
        product = opp["product"]
        print(f"--- {product['name'][:50]} ---")
        print(f"    Impressions: {opp['impressions']} | Clicks: {opp['clicks']} | CTR: {opp['ctr']:.2%}")
        print(f"    Existing pins: {opp['existing_pin_count']}")
        
        try:
            # Generate fresh variation
            pin = generate_fresh_variation(product, opp["existing_titles"])
            if not pin:
                print("  ✗ Failed to generate variation")
                has_errors = True
                continue
            
            print(f"  New angle: {pin['title'][:50]}")
            
            # Get background image
            pexels_url, _ = get_pexels_image(pin.get('pexels_search', product.get('category', 'fashion')))
            if not pexels_url:
                log.error("No Pexels image found")
                has_errors = True
                continue
            
            # Create the pin image
            img_bytes = create_pin_image(
                "product_hero",
                pexels_url,
                product.get('image_url', 'https://via.placeholder.com/800'),
                pin['title'],
                price=product.get('price')
            )
            pin['image_url'] = upload_image(img_bytes)
            
            # Save to Supabase
            affiliate_url = get_affiliate_url(product['asin'], product.get('affiliate_url'))
            board_id = get_board_for_product(product)
            
            row = {
                "title":         pin["title"],
                "description":   pin["description"],
                "alt_text":      pin.get("alt_text", ""),
                "keywords":      pin.get("keywords", []),
                "image_url":     pin["image_url"],
                "link_url":      affiliate_url,
                "product_id":    product["id"],
                "board_id":      board_id,
                "template_style": "product_hero",
                "approved":      True,   # Auto-approve repin variations
                "posted":        False
            }
            supabase_post("pins", row)
            print(f"  ✓ Created fresh variation (auto-approved)")
            created += 1
            
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            has_errors = True
        
        time.sleep(2)  # Brief pause between products
    
    print(f"\n{'='*60}")
    print(f"Done. Created {created} fresh pin variations from {len(opportunities)} opportunities.")
    print("="*60)
    
    if has_errors:
        sys.exit(1)
    else:
        sys.exit(0)
