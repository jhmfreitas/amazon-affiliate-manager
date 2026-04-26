-- ─────────────────────────────────────────────────────────────
-- Run this in Supabase → SQL Editor
-- Products table — stores every product you want to promote
-- ─────────────────────────────────────────────────────────────

create table products (
  id              bigserial primary key,
  asin            text not null unique,   -- Amazon product ID e.g. B08N5WRWNW
  name            text not null,          -- human readable name
  category        text not null,          -- e.g. "home office", "kitchen"
  niche           text not null,          -- Pinterest niche e.g. "remote work"
  audience        text not null,          -- e.g. "remote workers, freelancers"
  commission      numeric(4,2) default 0, -- Amazon commission % e.g. 4.50
  affiliate_url   text,                   -- stored once, reused forever
  score           numeric(6,2) default 0, -- updated weekly by score_products.py
  score_reason    text,                   -- why it scored this way
  bsr_rank        int,                    -- Amazon Best Sellers Rank
  trend_dir       text default 'stable',  -- "rising" | "stable" | "falling"
  trend_score     numeric(4,1) default 0, -- Google Trends interest 0-100
  pinterest_saves int default 0,          -- saves on your pins for this product
  active          boolean default true,   -- set false to pause a product
  last_scored_at  timestamptz,
  created_at      timestamptz default now()
);

-- Fast query for scoring and generation
create index on products (active, score desc);
create index on products (asin);

-- RLS — authenticated users only (matches your existing setup)
alter table products enable row level security;

create policy "auth users can read products"
  on products for select
  using (auth.role() = 'authenticated');

create policy "auth users can insert products"
  on products for insert
  with check (auth.role() = 'authenticated');

create policy "auth users can update products"
  on products for update
  using (auth.role() = 'authenticated');

-- service_role (GitHub Actions) bypasses RLS — no extra policy needed

-- ─────────────────────────────────────────────────────────────
-- Also add link_url to your existing pins table if not there yet
-- ─────────────────────────────────────────────────────────────
alter table pins add column if not exists link_url   text;
alter table pins add column if not exists product_id bigint references products(id);
alter table pins add column if not exists posted_at  timestamptz;

-- ─────────────────────────────────────────────────────────────
-- Seed your first products — edit these to match what you sell
-- ─────────────────────────────────────────────────────────────
insert into products (asin, name, category, niche, audience, commission) values
  ('B08N5WRWNW', 'FlexiSpot E7 Standing Desk', 'home office', 'standing desk', 'remote workers, freelancers', 3.00),
  ('B07K1RZWMC', 'Autonomous SmartDesk Pro',   'home office', 'standing desk', 'remote workers, entrepreneurs', 3.00),
  ('B09B8YWXDF', 'Logitech MX Master 3S Mouse','home office', 'desk setup',    'remote workers, programmers', 3.00);

-- ─────────────────────────────────────────────────────────────
-- Add columns for updated pipeline (run once)
-- ─────────────────────────────────────────────────────────────
alter table products add column if not exists price        numeric(8,2);
alter table products add column if not exists trend_delta  numeric(6,2) default 0;
alter table products add column if not exists save_delta   int default 0;

-- Verify
select id, asin, name, score, price, commission, active from products;
