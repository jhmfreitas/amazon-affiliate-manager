-- Product lifecycle timestamps for the retirement workflow.
-- Run once in Supabase SQL Editor before enabling retire_products.yml.

ALTER TABLE products
  ADD COLUMN IF NOT EXISTS paused_at timestamptz,
  ADD COLUMN IF NOT EXISTS retired_at timestamptz;

-- Existing paused rows predate paused_at. Use their final scoring time as the
-- best available approximation, falling back to creation time when necessary.
UPDATE products
SET paused_at = COALESCE(last_scored_at, created_at)
WHERE active = false
  AND retired_at IS NULL
  AND paused_at IS NULL;

CREATE INDEX IF NOT EXISTS products_retirement_queue_idx
  ON products (active, paused_at)
  WHERE retired_at IS NULL;

CREATE INDEX IF NOT EXISTS products_purge_queue_idx
  ON products (retired_at)
  WHERE retired_at IS NOT NULL;
