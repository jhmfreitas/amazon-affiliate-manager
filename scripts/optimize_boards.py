"""
optimize_boards.py
──────────────────
Automatically rewrites Pinterest board titles and descriptions using Gemini AI
to maximize SEO and conversion intent.
"""

import os
import json
import re
import requests
from pinterest_auth import PinterestAuth

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
PINTEREST_API  = "https://api.pinterest.com/v5"

def get_boards(auth):
    """Fetch all boards for the user."""
    resp = auth.get(f"{PINTEREST_API}/boards")
    resp.raise_for_status()
    return resp.json().get("items", [])

def generate_seo_metadata(board_name):
    """Use Gemini to create a high-SEO title and description."""
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"Optimize this Pinterest board name for high-conversion SEO: '{board_name}'.\n" \
             f"Provide a new 'name' (max 50 chars) and a 'description' (200-400 chars).\n" \
             f"Use keywords like 'Aesthetic', 'Home Decor', 'Inspiration', 'Essentials', 'Ideas'.\n" \
             f"Return as JSON: {{'name': '...', 'description': '...'}}"
             
    resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = resp.json()
    
    if "candidates" not in data:
        print(f"  Gemini failed for {board_name}")
        return None
        
    txt = data['candidates'][0]['content']['parts'][0]['text']
    try:
        # Better JSON extraction (handles markdown blocks)
        match = re.search(r"(\{.*\})", txt, re.S)
        if not match: 
            print(f"  Debug: AI didn't return valid JSON. Raw: {txt[:100]}...")
            return None
        seo = json.loads(match.group(1))
        
        # Pinterest hard limit: 50 chars for Name
        if len(seo.get('name', '')) > 50:
            seo['name'] = seo['name'][:47].strip() + "..."
            
        return seo
    except Exception as e:
        print(f"  Error parsing AI output: {e}")
        print(f"  Debug: Raw AI text was: {txt}")
        return None

def update_board(auth, board_id, metadata):
    """Update board name and description via Pinterest API."""
    url = f"{PINTEREST_API}/boards/{board_id}"
    
    # Pinterest API v5 uses PATCH for updates
    resp = requests.patch(url, headers=auth.headers(), json=metadata)
    if not resp.ok:
        print(f"  Failed to update {board_id}: {resp.text}")
        return False
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Pinterest Board SEO Optimizer (Debug Edition)")
    print("=" * 60)

    auth = PinterestAuth()
    boards = get_boards(auth)
    print(f"Found {len(boards)} boards to optimize.")

    # List of keywords that mean a board is already "Premium"
    SEO_MARKERS = ["|", ":", "&", "Aesthetic", "Ideas", "Inspiration"]

    for b in boards:
        # Skip system boards
        if b['name'].lower() in ['pins', 'all pins', 'guardar rápido']: continue
        
        # Skip if already looks optimized (contains SEO markers)
        is_optimized = any(m in b['name'] for m in SEO_MARKERS)
        if is_optimized and len(b['name']) > 20: # Long names with markers are done
            print(f"Skipping already optimized board: '{b['name']}'")
            continue
            
        print(f"\nOptimizing board: '{b['name']}'...")
        seo = generate_seo_metadata(b['name'])
        
        if seo:
            print(f"  New Name: {seo['name']}")
            if update_board(auth, b['id'], seo):
                print("  ✓ Board updated successfully.")
            else:
                print("  ✗ Update failed.")

    print("\nOptimization Complete.")
