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


-- Drop the old open policies
drop policy if exists "anon can read pins" on pins;
drop policy if exists "anon can update approval" on pins;

-- Anyone can read (needed for the login screen to not error)
create policy "read pins"
  on pins for select using (true);

-- Only authenticated users can insert, update, delete
create policy "auth users can update"
  on pins for update using (auth.role() = 'authenticated');

create policy "auth users can delete"
  on pins for delete using (auth.role() = 'authenticated');

-- Allowlist table — only you edit this directly in Supabase dashboard
create table allowed_users (
  email text primary key,
  name  text,
  added_at timestamptz default now()
);

-- RLS on allowed_users — nobody can read or write it via the API
-- Only service_role (your Python scripts + Supabase dashboard) can touch it
alter table allowed_users enable row level security;
-- No policies = total lockdown via API. You manage it only via dashboard.

-- Update pins policies to check against the allowlist table
drop policy if exists "auth users can update" on pins;
drop policy if exists "auth users can delete" on pins;
drop policy if exists "owner can update" on pins;
drop policy if exists "owner can delete" on pins;

create policy "allowlist can update"
  on pins for update
  using (
    exists (
      select 1 from allowed_users
      where email = auth.jwt() ->> 'email'
    )
  );

create policy "allowlist can delete"
  on pins for delete
  using (
    exists (
      select 1 from allowed_users
      where email = auth.jwt() ->> 'email'
    )
  );


  create or replace function public.check_user_allowed()
returns trigger language plpgsql security definer as $$
begin
  if not exists (
    select 1 from public.allowed_users
    where email = new.email
  ) then
    raise exception 'Access denied: % is not an allowed user.', new.email;
  end if;
  return new;
end;
$$;

-- Fire it on every new login
create trigger enforce_allowlist
  before insert on auth.users
  for each row execute function public.check_user_allowed();


ALTER TABLE pins ADD COLUMN template_style text;
