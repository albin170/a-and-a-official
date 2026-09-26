-- ============================================================
-- A MUSIC — Supabase schema
-- How to run: Supabase Dashboard → SQL Editor → New query →
-- paste this whole file → Run. Safe to run twice.
-- ============================================================

create table if not exists admins(
  admin_id text primary key,
  name text,
  password_hash text,
  salt text,
  role text default 'admin',
  created_at timestamptz
);

create table if not exists admin_tokens(
  token text primary key,
  admin_id text,
  expires_at timestamptz
);

create table if not exists songs(
  song_id text primary key,
  title text,
  artist text,
  category text,
  audio_url text,
  thumbnail_url text default '',
  description text default '',
  tags text default '',
  featured int default 0,
  enabled int default 1,
  play_count int default 0,
  created_at timestamptz
);

create table if not exists sound_effects(
  effect_id text primary key,
  name text,
  icon text default '😂',
  category text default 'Funny',
  audio_url text default '',
  trigger_mode text default 'random',
  frequency_min int default 30,
  frequency_max int default 120,
  probability int default 15,
  enabled int default 1,
  use_count int default 0
);

create table if not exists themes(
  theme_id text primary key,
  name text,
  primary_color text,
  secondary_color text,
  background text default 'dark',
  button_style text default 'rounded',
  font_family text default 'Inter',
  animation text default 'neon',
  player_style text default 'glass',
  site_name text default 'A Music',
  logo_url text default '',
  is_active int default 0
);

create table if not exists settings(
  key text primary key,
  value text
);

create table if not exists sessions(
  session_id text primary key,
  guest_id text,
  entry_time timestamptz,
  last_activity timestamptz,
  exit_time timestamptz,
  duration_sec int default 0,
  current_song text default '',
  songs_played int default 0,
  effects_used int default 0,
  theme text default ''
);

create table if not exists play_history(
  id text primary key,
  session_id text,
  song_id text,
  started_at timestamptz,
  stopped_at timestamptz,
  duration_sec int default 0,
  completed int default 0,
  skipped int default 0
);

-- Lock tables to the service_role key (the app uses it server-side).
alter table admins enable row level security;
alter table admin_tokens enable row level security;
alter table songs enable row level security;
alter table sound_effects enable row level security;
alter table themes enable row level security;
alter table settings enable row level security;
alter table sessions enable row level security;
alter table play_history enable row level security;

-- Storage buckets for uploads (public read so music/thumbnails play).
insert into storage.buckets (id, name, public)
values ('music','music',true), ('effects','effects',true), ('images','images',true)
on conflict (id) do update set public = true;

drop policy if exists "a-music public read" on storage.objects;
create policy "a-music public read"
on storage.objects for select
using (bucket_id in ('music','effects','images'));
