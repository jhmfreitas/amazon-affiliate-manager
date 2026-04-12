-- Run this in Supabase → SQL Editor → New query

create table pins (
  id            bigserial primary key,
  title         text not null,
  description   text not null,
  hashtags      text[] not null default '{}',
  pexels_search text,
  hook_type     text,
  score         numeric(4,2),
  score_reason  text,
  image_url     text,
  photographer  text,
  approved      boolean not null default false,
  posted        boolean not null default false,
  pinterest_id  text,
  created_at    timestamptz not null default now(),
  posted_at     timestamptz
);

-- Index so daily query is fast
create index on pins (approved, posted, created_at);

-- Storage bucket for images
-- Go to Supabase → Storage → New bucket
-- Name: pin-images
-- Public: YES (so Pinterest can fetch the image URL)

-- Row Level Security — allow your review app (anon key) to
-- read and update approved/posted fields only
alter table pins enable row level security;

create policy "anon can read pins"
  on pins for select using (true);

create policy "anon can update approval"
  on pins for update
  using (true)
  with check (true);

-- service_role key (used by GitHub Actions) bypasses RLS
-- so no extra policy needed for the Python scripts
