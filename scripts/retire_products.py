"""Retire inactive products and purge expired retirement records.

The scheduled job keeps the working product catalogue small without losing
recent history. It retires products that have remained paused for a configured
period, cancels their unposted pins, then purges their local records only after
the longer retention window has elapsed.
"""

import os
from datetime import datetime, timedelta, timezone

import requests

from config import SUPABASE_HEADERS, SUPABASE_URL, supabase_get, supabase_patch


def get_positive_days(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number") from error
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def supabase_delete(path):
    response = requests.delete(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers=SUPABASE_HEADERS,
    )
    response.raise_for_status()


def load_retireable_products(cutoff):
    return supabase_get("products", params={
        "active": "eq.false",
        "retired_at": "is.null",
        "paused_at": f"lt.{cutoff.isoformat()}",
        "select": "id,asin,name,pause_reason,paused_at",
        "order": "paused_at.asc",
    })


def load_purgeable_products(cutoff):
    return supabase_get("products", params={
        "retired_at": f"lt.{cutoff.isoformat()}",
        "select": "id,asin,name,retired_at",
        "order": "retired_at.asc",
    })


def retire_products(products, now, retire_after_days, dry_run):
    retired = 0
    for product in products:
        reason = product.get("pause_reason") or "Paused"
        retirement_reason = f"{reason}; retired after {retire_after_days} days paused"

        if dry_run:
            print(f"Would retire {product['asin']} ({product['name'][:60]})")
        else:
            # Prevent queued, unposted pins from promoting a retired product.
            supabase_patch(
                f"pins?product_id=eq.{product['id']}&posted=eq.false",
                {"approved": False},
            )
            supabase_patch(
                f"products?id=eq.{product['id']}",
                {"retired_at": now.isoformat(), "pause_reason": retirement_reason},
            )
            print(f"Retired {product['asin']} ({product['name'][:60]})")
        retired += 1
    return retired


def purge_products(products, dry_run):
    purged = 0
    for product in products:
        if dry_run:
            print(f"Would purge {product['asin']} and its local pins")
        else:
            # Product IDs are primary-key integers returned by Supabase, so the
            # two exact REST filters only target this retired product and its pins.
            supabase_delete(f"pins?product_id=eq.{product['id']}")
            supabase_delete(f"products?id=eq.{product['id']}")
            print(f"Purged {product['asin']} and its local pins")
        purged += 1
    return purged


def main():
    retire_after_days = get_positive_days("RETIRE_AFTER_DAYS", 45)
    purge_after_days = get_positive_days("PURGE_AFTER_DAYS", 180)
    dry_run = os.environ.get("RETIRE_DRY_RUN", "0") == "1"
    purge_retired = os.environ.get("PURGE_RETIRED", "1") == "1"

    if purge_after_days <= retire_after_days:
        raise ValueError("PURGE_AFTER_DAYS must be greater than RETIRE_AFTER_DAYS")

    now = datetime.now(timezone.utc)
    retireable = load_retireable_products(now - timedelta(days=retire_after_days))
    retired = retire_products(retireable, now, retire_after_days, dry_run)

    purged = 0
    if purge_retired:
        purgeable = load_purgeable_products(now - timedelta(days=purge_after_days))
        purged = purge_products(purgeable, dry_run)

    mode = "DRY RUN" if dry_run else "LIVE"
    print(f"{mode} complete: {retired} retired, {purged} purged")


if __name__ == "__main__":
    main()
