# Scoring Strategy — Product Prioritization Engine

> **Last updated**: 2026-05-16  
> **Status**: Steps 1–3 implemented ✅ | Steps 4–5 planned

---

## Overview

The scoring engine (`scripts/score_products.py`) runs weekly and decides which
products get promoted on Pinterest and which get auto-paused. It directly
controls revenue by determining where pin slots are allocated.

---

## Architecture

```
sync_metrics.py (daily)          score_products.py (weekly)
─────────────────────           ──────────────────────────
Pinterest API                    Amazon BSR scraper
  → impressions                    → BSR rank
  → clicks                        → price
  → saves                         → availability
  → aggregate to products       Google Trends
                                   → interest score
                                   → direction (rising/stable/falling)
                                Pinterest saves + pin count
                                   → from live API
                                ──────────────────────────
                                Deterministic Formula
                                   → score 0-100
                                   → auditable breakdown
                                   → auto-pause decisions
                                   → save to Supabase
                                ──────────────────────────
                                         │
                                         ▼
                                generate_pins.py (daily)
                                   → picks top-scored active products
                                   → allocates pin slots
```

---

## Scoring Formula (Deterministic)

**Total: 100 points max**

### 1. Pinterest Performance — 40 points (THE MONEY SIGNAL)

Uses real metrics from `sync_metrics.py` (already on the `products` table):

| Sub-signal | Max pts | How |
|---|---|---|
| **CTR** (clicks / impressions) | 20 | ≥5% = 20, ≥2% = 15, ≥1% = 10, >0 = 5, 0 = 0 |
| **Engagement rate** ((clicks+saves) / impressions) | 10 | ≥8% = 10, ≥3% = 7, ≥1% = 4, else = 1 |
| **Volume bonus** (total impressions) | 10 | ≥1000 = 10, ≥500 = 7, ≥100 = 4, else = 2 |

> **New products** (0 impressions) receive a 15-point bonus so they get a fair
> trial before being judged against established products.

### 2. Revenue Potential — 25 points

```
payout = price × (commission% / 100)
```

| Payout per sale | Points |
|---|---|
| ≥ £5.00 | 25 |
| ≥ £2.00 | 18 |
| ≥ £1.00 | 12 |
| ≥ £0.50 | 6 |
| < £0.50 | 2 |

### 3. Market Demand — 20 points

| Signal | Max pts | Tiers |
|---|---|---|
| **Amazon BSR** | 10 | <1K = 10, <10K = 8, <50K = 5, <100K = 3, unknown = 4 |
| **Google Trends** (0-100 interest) | 10 | ≥70 = 10, ≥40 = 7, ≥20 = 4, else = 1 |

### 4. Momentum — 15 points

| Signal | Max pts | Tiers |
|---|---|---|
| **Trend direction** | 8 | rising = 8, stable = 4, falling = 0 |
| **Save delta** (week-over-week) | 7 | >5 = 7, >0 = 5, flat = 2, negative = 0 |

---

## Auto-Pause Rules

Products are automatically paused (set `active=false`) when any of these
conditions are met. Each pause records a `pause_reason` in the database.

### Amazon-Level Pauses (checked first)

| Condition | Pause reason |
|---|---|
| Product unavailable (404 or "currently unavailable") | `Product unavailable on Amazon` |
| Price dropped below £10 | `Price too low (£X.XX)` |

### Pinterest Performance Pauses (checked after Amazon)

These only trigger after a **fair trial period**:

| Condition | Pause reason |
|---|---|
| 3+ pins, 7+ days since first pin, zero clicks AND zero saves | `Zero engagement (X pins, Yd, Z imp)` |
| Falling Google Trend + zero clicks, 2+ pins | `Falling trend with no click-through` |

**Fair trial tracking**: Uses the `created_at` of the earliest posted pin for
the product (queried from the `pins` table), NOT `last_pinned_at` which updates
every time a new pin is created.

**Key principle**: Clicks are the primary signal, not saves. A product with
impressions and clicks but no saves is still generating affiliate revenue.
A product with saves but no clicks is not.

---

## What Changed (May 2026 Overhaul)

### Before (v1)
- Scoring delegated to Gemini LLM → non-deterministic, unauditable
- Only used Pinterest saves (weakest signal)
- Binary active/pause with no reason tracking
- Trial period used `last_pinned_at` (wrong — resets every pin)
- No revenue-potential calculation
- `GEMINI_API_KEY` required for scoring

### After (v2 — current)
- Deterministic formula → same inputs always give same score
- Uses impressions, clicks, AND saves from `sync_metrics.py`
- `pause_reason` column explains every pause decision
- Trial period uses earliest pin `created_at` (correct)
- Revenue potential = `price × commission%` weighted at 25%
- `GEMINI_API_KEY` optional (only used for keyword research)

---

## Implementation Status

| Step | Description | Status |
|---|---|---|
| 1 | Query earliest pin date for fair trial period | ✅ Done |
| 2 | Use `total_impressions` + `total_clicks` in scoring | ✅ Done |
| 3 | Replace Gemini scoring with deterministic formula | ✅ Done |
| 4 | Add `lifecycle_tier` column (testing/scaling/maintaining/sunset) | 🔲 Planned |
| 5 | Update `generate_pins.py` rotation to use tiers | 🔲 Planned |
| 6 | Creative A/B tracking (distinguish bad pin vs bad product) | 🔲 Future |

---

## Next Phase: Lifecycle Tiers (Steps 4-5)

The current system is still binary (active / paused). A more mature approach
uses lifecycle tiers to control pin allocation frequency:

```
🧪 TESTING ──────► 🚀 SCALING ──────► 📊 MAINTAINING ──────► 🌅 SUNSET
(new, < 3 pins)    (CTR > 1%)         (steady clicks)        (declining)
1 pin/week         3 pins/week        1 pin/week             0 pins
```

### Tier Transition Rules (proposed)

| From | To | Condition |
|---|---|---|
| 🧪 Testing | 🚀 Scaling | CTR > 1% after 3+ pins |
| 🧪 Testing | 🌅 Sunset | 0 clicks after 5+ pins, 14d |
| 🚀 Scaling | 📊 Maintaining | CTR declining for 2 consecutive weeks |
| 🚀 Scaling | 🚀 Scaling (stay) | CTR still > 2% |
| 📊 Maintaining | 🌅 Sunset | Clicks drop to 0 for 14+ days |
| 🌅 Sunset | 🧪 Testing | Manual reactivation |

### Database changes needed

```sql
-- Step 4: Add lifecycle tier
ALTER TABLE products
ADD COLUMN IF NOT EXISTS lifecycle_tier text DEFAULT 'testing'
CHECK (lifecycle_tier IN ('testing', 'scaling', 'maintaining', 'sunset'));

COMMENT ON COLUMN products.lifecycle_tier
IS 'Product lifecycle stage: testing, scaling, maintaining, or sunset';
```

### `generate_pins.py` changes needed (Step 5)

The rotation logic should allocate pins based on tier:

```python
TIER_PIN_ALLOCATION = {
    "scaling":     3,   # 3 pins per cycle
    "testing":     1,   # 1 pin per cycle (gathering data)
    "maintaining": 1,   # 1 pin per cycle (steady state)
    "sunset":      0,   # no new pins
}
```

---

## Gemini's Remaining Role

Gemini is still used for tasks where LLMs excel (creativity, language), but no
longer for scoring (a math problem):

| Task | Uses Gemini? | Why |
|---|---|---|
| Product scoring | ❌ No | Deterministic formula is more reliable |
| Keyword research | ✅ Yes | Refining autocomplete suggestions needs language understanding |
| Pin copy generation | ✅ Yes | Creative writing is an LLM strength |
| Pin image search queries | ✅ Yes | Semantic understanding of product → aesthetic |

---

## Database Schema (scoring-related columns)

```sql
-- On the products table:
score                    numeric     -- 0-100 deterministic score
score_reason             text        -- auditable breakdown string
bsr_rank                 integer     -- Amazon Best Sellers Rank
trend_score              numeric     -- Google Trends 0-100
trend_dir                text        -- 'rising', 'stable', 'falling'
trend_delta              numeric     -- week-over-week trend change
pinterest_saves          integer     -- total saves (from scoring run)
save_delta               integer     -- week-over-week save change
total_impressions        integer     -- aggregated from pins (sync_metrics.py)
total_clicks             integer     -- aggregated from pins (sync_metrics.py)
total_saves              integer     -- aggregated from pins (sync_metrics.py)
pinterest_keywords       jsonb       -- array of keyword strings
keywords_last_updated_at timestamptz -- when keywords were last refreshed
last_scored_at           timestamptz -- when scoring last ran
last_pinned_at           timestamptz -- when last pin was created
active                   boolean     -- whether product gets new pins
pause_reason             text        -- why it was paused (NULL when active)
-- lifecycle_tier         text        -- PLANNED: testing/scaling/maintaining/sunset
```
