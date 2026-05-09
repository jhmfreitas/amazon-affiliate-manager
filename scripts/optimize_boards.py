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
    return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))

def update_board(auth, board_id, metadata):
    """Update board name and description via Pinterest API."""
    url = f"{PINTEREST_API}/boards/{board_id}"
    resp = auth.post(url, json=metadata) # API uses PATCH but my auth helper handles it
    
    # Note: Pinterest API v5 actually uses PATCH for updates.
    # My pinterest_auth.py has post/get, I should check if it needs a patch method.
    # Actually, I'll just use requests.patch with auth.headers() directly.
    
    resp = requests.patch(url, headers=auth.headers(), json=metadata)
    if not resp.ok:
        print(f"  Failed to update {board_id}: {resp.text}")
        return False
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Pinterest Board SEO Optimizer")
    print("=" * 60)

    auth = PinterestAuth()
    boards = get_boards(auth)
    print(f"Found {len(boards)} boards to optimize.")

    for b in boards:
        # Skip system boards if any
        if b['name'].lower() in ['pins', 'all pins']: continue
        
        print(f"\nOptimizing board: '{b['name']}'...")
        seo = generate_seo_metadata(b['name'])
        
        if seo:
            print(f"  New Name: {seo['name']}")
            print(f"  New Desc: {seo['description'][:70]}...")
            
            if update_board(auth, b['id'], seo):
                print("  ✓ Board updated successfully.")
            else:
                print("  ✗ Update failed.")

    print("\nOptimization Complete.")
