-- BI Trust Platform: database schema (Postgres / Supabase).
--
-- Run it once in Supabase: Dashboard -> SQL Editor -> paste -> Run
-- (or `supabase db push`, which applies supabase/migrations/). Safe to run
-- again: everything is "if not exists". The API also applies it on start
-- unless BIP_DB_AUTO_SCHEMA=false.
--
--   bip.users      accounts: username, display name, salted scrypt password hash
--   bip.documents  every record (dashboards, files, datasets, results,
--                  templates, investigations) as JSON, with the username
--                  that owns it; the API only ever shows people their own rows
--   bip.counters   running numbers for readable ids (CONN0001, FILE0001, ...)
--   bip.file_parts uploaded Excel/CSV files, in parts of a few MB, when no
--                  storage bucket is set up (see app/store/blobs.py)
--
-- Only the API (connecting with the database password) uses these tables.
-- They live in a private schema with row-level security on and no policies,
-- so Supabase's public REST API (anon / publishable key) can't read them.

create schema if not exists bip;
create table if not exists bip.documents (
    collection text not null,
    id         text not null,
    owner      text not null,
    created_at timestamptz not null default now(),
    seq        bigserial,
    data       text not null,
    primary key (collection, id)
);
create index if not exists documents_owner_idx on bip.documents (collection, owner, created_at desc, seq desc);
create table if not exists bip.counters (prefix text primary key, value bigint not null);
create table if not exists bip.users (
    id            text primary key,
    username      text not null unique,
    display_name  text,
    password_hash text not null,
    created_at    timestamptz not null default now(),
    last_login_at timestamptz,
    token_version integer not null default 0,
    disabled      boolean not null default false
);
-- only the API (connecting as the database owner) reads these tables: no
-- row-level policies means nothing is reachable through Supabase's public APIs
alter table bip.documents enable row level security;
alter table bip.counters enable row level security;
create table if not exists bip.file_parts (
    key        text not null,
    part       integer not null,
    owner      text not null,
    data       bytea not null,
    created_at timestamptz not null default now(),
    primary key (key, part)
);
alter table bip.users enable row level security;
alter table bip.file_parts enable row level security;
revoke all on schema bip from public;
do $$ begin
  if exists (select 1 from pg_roles where rolname = 'anon') then execute 'revoke all on schema bip from anon'; end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then execute 'revoke all on schema bip from authenticated'; end if;
end $$;
