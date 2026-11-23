-- Minimal stand-in for the parts of Supabase the ref migration relies on, so the migration and its
-- row-level security can be tested on plain Postgres. Mirrors Supabase's own definitions of auth.jwt()
-- and auth.uid() (claims come from the request.jwt.claims setting that PostgREST sets per request).
do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select from pg_roles where rolname = 'supabase_auth_admin') then create role supabase_auth_admin nologin; end if;
end $$;

create schema if not exists extensions;
create schema if not exists auth;
grant usage on schema auth to anon, authenticated, supabase_auth_admin;

create table if not exists auth.users (id uuid primary key, email text);

create or replace function auth.jwt() returns jsonb language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create or replace function auth.uid() returns uuid language sql stable as $$
  select nullif(auth.jwt() ->> 'sub', '')::uuid
$$;
