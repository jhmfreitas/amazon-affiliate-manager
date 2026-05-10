"""
sync_metrics.py
───────────────
Fetches real-time performance data from Pinterest API for all posted pins.
Updates both individual 'pins' and aggregate 'products' metrics.
"""

import json
import os
import requests
from datetime import datetime, timedelta, timezone
from config import SUPABASE_URL, SUPABASE_HEADERS
from pinterest_auth import PinterestAuth

PINTEREST_API = "https://api.pinterest.com/v5"
METRIC_TYPES = ("IMPRESSION", "PIN_CLICK", "SAVE")
NEW_PIN_ANALYTICS_GRACE_HOURS = int(os.environ.get("PINTEREST_METRICS_GRACE_HOURS", "48"))


def empty_metrics(source="none"):
    return {
        "impressions": 0,
        "clicks": 0,
        "saves": 0,
        "source": source,
    }


def metric_to_int(value):
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in ("value", "total", "count"):
            if key in value:
                return metric_to_int(value[key])
        return 0
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def metric_value(metrics, *names):
    if not isinstance(metrics, dict):
        return 0

    lowered = {str(k).lower(): v for k, v in metrics.items()}
    for name in names:
        if name in metrics:
            return metric_to_int(metrics[name])

        value = lowered.get(name.lower())
        if value is not None:
            return metric_to_int(value)

    return 0


def metrics_have_values(metrics):
    return any(metrics.get(key, 0) > 0 for key in ("impressions", "clicks", "saves"))


def parse_timestamp(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def pin_age_hours(pin):
    posted_at = parse_timestamp(pin.get("posted_at") or pin.get("created_at"))
    if not posted_at:
        return None

    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)

    age = datetime.now(timezone.utc) - posted_at
    return max(age.total_seconds() / 3600, 0)


def should_skip_zero_update(pin, metrics):
    age_hours = pin_age_hours(pin)
    if age_hours is None:
        return False

    return (
        not metrics_have_values(metrics)
        and age_hours < NEW_PIN_ANALYTICS_GRACE_HOURS
    )


def merge_metrics(*metric_sets):
    merged = empty_metrics()
    sources = []

    for metrics in metric_sets:
        if not metrics:
            continue

        for key in ("impressions", "clicks", "saves"):
            merged[key] = max(merged[key], metrics.get(key, 0))

        source = metrics.get("source")
        if source:
            sources.append(source)

    merged["source"] = "+".join(sources) if sources else "none"
    return merged


def sum_metrics(metric_sets, source):
    totals = empty_metrics(source)

    for metrics in metric_sets:
        if not metrics:
            continue
        for key in ("impressions", "clicks", "saves"):
            totals[key] += metrics.get(key, 0)

    return totals


def metrics_from_metric_dict(metrics, source):
    return {
        "impressions": metric_value(metrics, "IMPRESSION", "impression", "impressions"),
        "clicks": metric_value(metrics, "PIN_CLICK", "pin_click", "pin_clicks", "clickthrough"),
        "saves": metric_value(metrics, "SAVE", "save", "saves", "repin", "repins"),
        "source": source,
    }


def analytics_debug_summary(data):
    all_data = data.get("all", {}) if isinstance(data, dict) else {}
    if not isinstance(all_data, dict):
        return None

    daily_metrics = all_data.get("daily_metrics", []) or []
    status_counts = {}
    for row in daily_metrics:
        if not isinstance(row, dict):
            continue
        status = row.get("data_status", "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "summary_metrics": all_data.get("summary_metrics", {}),
        "data_status_counts": status_counts,
        "recent_daily_metrics": daily_metrics[-7:],
    }


def maybe_debug_response(pinterest_id, label, data):
    if os.environ.get("DEBUG_PINTEREST_METRICS") != "1":
        return

    print(f"  Debug {label} response for {pinterest_id}:")
    if label == "analytics":
        print(json.dumps(analytics_debug_summary(data), indent=2)[:4000])
        if os.environ.get("DEBUG_PINTEREST_METRICS_FULL") != "1":
            return

    print(json.dumps(data, indent=2)[:4000])


def parse_analytics_metrics(data):
    """Parse /pins/{id}/analytics response totals.

    Pinterest returns daily rows under all.daily_metrics, not direct totals.
    Keep support for direct totals in case Pinterest changes the shape again.
    """
    all_data = data.get("all", {}) if isinstance(data, dict) else {}
    metric_sets = []

    if isinstance(all_data, dict):
        summary = metrics_from_metric_dict(all_data.get("summary_metrics", {}), "analytics:summary")
        if metrics_have_values(summary):
            return summary

        direct = metrics_from_metric_dict(all_data, "analytics")
        if metrics_have_values(direct):
            return direct

        for daily in all_data.get("daily_metrics", []) or []:
            if not isinstance(daily, dict):
                continue
            metric_sets.append(metrics_from_metric_dict(daily.get("metrics", {}), "analytics"))

    return sum_metrics(metric_sets, "analytics") if metric_sets else empty_metrics("analytics")


def parse_pin_metrics(data):
    """Parse pin_metrics=true lifetime metrics from a Get Pin response."""
    pin_metrics = data.get("pin_metrics", {}) if isinstance(data, dict) else {}
    metric_sets = []

    if isinstance(pin_metrics, dict):
        for key in ("lifetime_metrics", "all_time", "all_time_metrics", "90d", "30d"):
            section = pin_metrics.get(key)
            if isinstance(section, dict):
                metric_sets.append(metrics_from_metric_dict(section, f"pin_metrics:{key}"))

    return merge_metrics(*metric_sets) if metric_sets else empty_metrics("pin_metrics")

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

def analytics_start_date(pin=None):
    oldest_allowed = datetime.now(timezone.utc) - timedelta(days=89)
    posted_at = parse_timestamp((pin or {}).get("posted_at") or (pin or {}).get("created_at"))

    if posted_at:
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        # Include the day before posting to avoid timezone edge cases.
        start_dt = max(oldest_allowed, posted_at - timedelta(days=1))
    else:
        start_dt = oldest_allowed

    return start_dt.strftime("%Y-%m-%d")


def fetch_pin_metrics(auth, pinterest_id, pin=None):
    """Call Pinterest API to get analytics for a specific pin."""
    params = {
        "start_date":   analytics_start_date(pin),
        "end_date":     datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "app_types":    "ALL",
        "split_field":  "NO_SPLIT",
        "metric_types": ",".join(METRIC_TYPES)
    }

    url = f"{PINTEREST_API}/pins/{pinterest_id}/analytics"
    resp = auth.get(url, params=params)
    if resp.status_code == 404:
        return None # Pin might have been deleted or is too new

    if not resp.ok:
        print(f"  Pinterest Error for {pinterest_id}: {resp.text}")
        return None

    data = resp.json()
    maybe_debug_response(pinterest_id, "analytics", data)
    analytics_metrics = parse_analytics_metrics(data)

    # The analytics endpoint is date-range based. If it returns no ready data,
    # fall back to the Pin metadata endpoint's lifetime/rolling metrics.
    lifetime_metrics = None
    if not metrics_have_values(analytics_metrics):
        meta_resp = auth.get(
            f"{PINTEREST_API}/pins/{pinterest_id}",
            params={"pin_metrics": "true"}
        )
        if meta_resp.ok:
            meta_data = meta_resp.json()
            maybe_debug_response(pinterest_id, "pin_metrics", meta_data)
            lifetime_metrics = parse_pin_metrics(meta_data)
        elif meta_resp.status_code != 404:
            print(f"  Pinterest metadata error for {pinterest_id}: {meta_resp.text}")

    return merge_metrics(analytics_metrics, lifetime_metrics)

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

        age_hours = pin_age_hours(pin)
        age_label = f", posted {age_hours:.1f}h ago" if age_hours is not None else ""
        print(f"  Syncing Pin {pin['id']} ({pid}{age_label})...")
        metrics = fetch_pin_metrics(auth, pid, pin)
        
        if metrics:
            if should_skip_zero_update(pin, metrics):
                print(
                    f"    ~ Pinterest returned zeroes, but this pin is under "
                    f"{NEW_PIN_ANALYTICS_GRACE_HOURS}h old. Skipping update for now."
                )
                continue

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
            print(
                f"    ✓ Imp: {metrics['impressions']} | "
                f"Clicks: {metrics['clicks']} | Saves: {metrics['saves']} "
                f"({metrics['source']})"
            )

    if updated_count > 0:
        update_aggregate_products()
    
    print("\nSync Complete.")
