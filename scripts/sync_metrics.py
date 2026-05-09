"""
sync_metrics.py
───────────────
Fetches real-time performance data from Pinterest API for all posted pins.
Updates both individual 'pins' and aggregate 'products' metrics.
"""

import os
import requests
from datetime import datetime, timezone
from config import (
    SUPABASE_URL, SUPABASE_HEADERS,
    supabase_get, supabase_patch, log
)
from pinterest_auth import PinterestAuth

PINTEREST_API = "https://api.pinterest.com/v5"

def get_posted_pins():
    """Fetch all pins that have a Pinterest ID and haven't been synced in the last 24h."""
    # We fetch all posted pins to keep metrics fresh
    resp = requests.get(
        f"{SUPABASE_URL}/rest/v1/pins",
        headers=SUPABASE_HEADERS,
        params={
            "posted":       "eq.true",
            "pinterest_id": "not.is.null",
            "order":        "created_at.desc"
        }
    )
    resp.raise_for_status()
    return resp.json()

def fetch_pin_metrics(auth, pinterest_id):
    """Call Pinterest API to get analytics for a specific pin."""
    # Metric types: IMPRESSION, PIN_CLICK, SAVE
    url = f"{PINTEREST_API}/pins/{pinterest_id}/analytics"
    params = {
        "start_date":   "2024-01-01", # High enough to catch everything
        "end_date":     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "metric_types": "IMPRESSION,PIN_CLICK,SAVE"
    }
    
    resp = auth.get(url, params=params)
    if resp.status_code == 404:
        return None # Pin might have been deleted or is too new
    
    if not resp.ok:
        print(f"  Pinterest Error for {pinterest_id}: {resp.text}")
        return None
        
    data = resp.json()
    # Pinterest returns a 'all' object with totals
    summary = data.get("all", {})
    return {
        "impressions": int(summary.get("IMPRESSION", 0)),
        "clicks":      int(summary.get("PIN_CLICK", 0)),
        "saves":       int(summary.get("SAVE", 0))
    }

def update_aggregate_products():
    """Sum up all pin metrics and save them to the products table."""
    print("Recalculating product aggregates...")
    
    # Get all products
    products = requests.get(f"{SUPABASE_URL}/rest/v1/products", headers=SUPABASE_HEADERS).json()
    
    for prod in products:
        # Sum metrics from all pins linked to this product
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/pins",
            headers=SUPABASE_HEADERS,
            params={
                "product_id": f"eq.{prod['id']}",
                "select":     "impressions,clicks,saves"
            }
        )
        pins = resp.json()
        
        total_imp   = sum(p.get("impressions", 0) for p in pins)
        total_clk   = sum(p.get("clicks", 0) for p in pins)
        total_sav   = sum(p.get("saves", 0) for p in pins)
        
        # Update product
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod['id']}",
            headers=SUPABASE_HEADERS,
            json={
                "total_impressions": total_imp,
                "total_clicks":      total_clk,
                "total_saves":       total_sav
            }
        )
    print("  ✓ Product aggregates updated.")

if __name__ == "__main__":
    print("=" * 60)
    print(f"Pinterest Metrics Sync — {datetime.now(timezone.utc)}")
    print("=" * 60)

    auth = PinterestAuth()
    pins = get_posted_pins()
    print(f"Found {len(pins)} posted pins to sync.")

    updated_count = 0
    for pin in pins:
        pid = pin["pinterest_id"]
        if pid == "unknown": continue
        
        print(f"  Syncing Pin {pin['id']} ({pid})...")
        metrics = fetch_pin_metrics(auth, pid)
        
        if metrics:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/pins?id=eq.{pin['id']}",
                headers=SUPABASE_HEADERS,
                json={
                    "impressions":    metrics["impressions"],
                    "clicks":         metrics["clicks"],
                    "saves":          metrics["saves"],
                    "last_synced_at": datetime.now(timezone.utc).isoformat()
                }
            )
            updated_count += 1
            print(f"    ✓ Imp: {metrics['impressions']} | Clicks: {metrics['clicks']} | Saves: {metrics['saves']}")

    if updated_count > 0:
        update_aggregate_products()
    
    print("\nSync Complete.")
