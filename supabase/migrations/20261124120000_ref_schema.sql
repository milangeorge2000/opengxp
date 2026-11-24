-- OpenGxP reference data (ref) schema for Supabase.
-- Design: docs/REFDATA.md. Data-level RBAC: row-level security on every table, driven by ref.user_roles.
-- Clients reach data only through SECURITY INVOKER functions in schema api (the only schema to expose in
-- PostgREST). Ingest uses its own role (ref_ingest) and never the service_role key.

create extension if not exists pg_trgm with schema extensions;

create schema if not exists ref;
create schema if not exists api;

revoke all on schema ref from public;
revoke all on schema api from public;
alter default privileges in schema ref revoke execute on functions from public;
alter default privileges in schema api revoke execute on functions from public;

do $$ begin
  if not exists (select from pg_roles where rolname = 'ref_ingest') then
    create role ref_ingest nologin;   -- grant it to a dedicated login user; never use service_role for ingest
  end if;
end $$;

-- ---------------------------------------------------------------- roles and access mapping
create table ref.roles (
  role        text primary key check (role ~ '^[a-z][a-z0-9_]{1,30}$'),
  description text not null
);

create table ref.role_datasets (
  role    text not null references ref.roles (role) on delete cascade,
  dataset text not null check (dataset in ('*', 'iid', 'psg', 'dissolution', 'dmf')),
  primary key (role, dataset)
);

create table ref.user_roles (
  user_id    uuid not null references auth.users (id) on delete cascade,
  role       text not null references ref.roles (role),
  granted_by text not null default coalesce(auth.uid()::text, current_user),
  granted_at timestamptz not null default now(),
  primary key (user_id, role)
);

create table ref.role_audit (
  id      bigint generated always as identity primary key,
  at      timestamptz not null default now(),
  action  text not null,
  user_id uuid not null,
  role    text not null,
  actor   text not null
);

create function ref.log_role_change() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if tg_op in ('INSERT', 'UPDATE') then
    insert into ref.role_audit (action, user_id, role, actor)
    values (lower(tg_op), new.user_id, new.role, coalesce(auth.uid()::text, session_user));
  end if;
  if tg_op in ('DELETE', 'UPDATE') then
    insert into ref.role_audit (action, user_id, role, actor)
    values (case when tg_op = 'DELETE' then 'delete' else 'update_old' end, old.user_id, old.role,
            coalesce(auth.uid()::text, session_user));
  end if;
  return null;
end $$;

create trigger user_roles_audit after insert or update or delete on ref.user_roles
  for each row execute function ref.log_role_change();

insert into ref.roles (role, description) values
  ('viewer',      'Read the Inactive Ingredient Database'),
  ('formulation', 'Formulation scientists: Inactive Ingredient Database'),
  ('generics',    'Generic development: IID, product-specific guidances, dissolution methods'),
  ('sourcing',    'API sourcing: Drug Master File and CEP data'),
  ('admin',       'All datasets');

insert into ref.role_datasets (role, dataset) values
  ('viewer', 'iid'), ('formulation', 'iid'),
  ('generics', 'iid'), ('generics', 'psg'), ('generics', 'dissolution'),
  ('sourcing', 'dmf'),
  ('admin', '*');

-- ---------------------------------------------------------------- versioned datasets
create table ref.dataset_versions (
  id            bigint generated always as identity primary key,
  dataset       text not null check (dataset in ('iid', 'psg', 'dissolution', 'dmf')),
  label         text not null,
  source_url    text not null check (source_url ~ '^https://'),
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  declared_rows integer not null check (declared_rows > 0),
  status        text not null default 'staged' check (status in ('staged', 'published', 'retired')),
  created_at    timestamptz not null default now(),
  created_by    text not null default current_user,
  published_at  timestamptz,
  unique (dataset, source_sha256)
);
create unique index dataset_versions_one_published on ref.dataset_versions (dataset) where status = 'published';

create table ref.iid_entries (
  version_id              bigint not null references ref.dataset_versions (id),
  row_no                  integer not null check (row_no > 0),
  ingredient_name         text not null check (ingredient_name <> ''),
  route                   text not null check (route <> ''),
  dosage_form             text not null check (dosage_form <> ''),
  cas_number              text check (cas_number ~ '^[0-9]{2,7}-[0-9]{2}-[0-9]$'),
  unii                    text check (unii ~ '^[0-9A-Z]{10}$'),
  potency_amount          numeric check (potency_amount >= 0),
  potency_unit            text,
  max_daily_exposure      numeric check (max_daily_exposure >= 0),
  max_daily_exposure_unit text,
  record_updated          boolean not null default false,
  primary key (version_id, row_no)
);
create index iid_entries_lookup on ref.iid_entries (version_id, ingredient_name, route, dosage_form);
create index iid_entries_unii on ref.iid_entries (version_id, unii);
create index iid_entries_name_trgm on ref.iid_entries using gin (ingredient_name extensions.gin_trgm_ops);

-- Published data is immutable: status only moves staged -> published -> retired, rows never change,
-- and only staged versions may be deleted (so a failed load can be retried).
create function ref.guard_versions() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'DELETE' then
    if old.status <> 'staged' then raise exception 'version % is %, it cannot be deleted', old.id, old.status; end if;
    return old;
  end if;
  if (new.id, new.dataset, new.label, new.source_url, new.source_sha256, new.declared_rows, new.created_at, new.created_by)
     is distinct from
     (old.id, old.dataset, old.label, old.source_url, old.source_sha256, old.declared_rows, old.created_at, old.created_by) then
    raise exception 'dataset version % is immutable except for its status', old.id;
  end if;
  if new.status is distinct from old.status and not (
       (old.status = 'staged' and new.status = 'published') or (old.status = 'published' and new.status = 'retired')) then
    raise exception 'status cannot change from % to %', old.status, new.status;
  end if;
  return new;
end $$;
create trigger dataset_versions_guard before update or delete on ref.dataset_versions
  for each row execute function ref.guard_versions();

create function ref.guard_entries() returns trigger
language plpgsql set search_path = '' as $$
declare s text;
begin
  if tg_op = 'UPDATE' then raise exception 'reference rows are immutable'; end if;
  select status into s from ref.dataset_versions where id = old.version_id;
  if s <> 'staged' then raise exception 'rows of a % version cannot be deleted', s; end if;
  return old;
end $$;
create trigger iid_entries_guard before update or delete on ref.iid_entries
  for each row execute function ref.guard_entries();

-- ---------------------------------------------------------------- access helpers
create function ref.can_read(p_dataset text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from ref.user_roles ur join ref.role_datasets rd on rd.role = ur.role
    where ur.user_id = auth.uid() and rd.dataset in (p_dataset, '*'))
$$;

create function ref.published_version(p_dataset text) returns bigint
language sql stable security definer set search_path = '' as $$
  select id from ref.dataset_versions where dataset = p_dataset and status = 'published'
$$;

create function ref.publish_version(p_id bigint) returns void
language plpgsql security definer set search_path = '' as $$
declare v ref.dataset_versions; n bigint;
begin
  select * into v from ref.dataset_versions where id = p_id for update;
  if not found then raise exception 'version % not found', p_id; end if;
  if v.status <> 'staged' then raise exception 'version % is %, not staged', p_id, v.status; end if;
  if v.dataset = 'iid' then
    select count(*) into n from ref.iid_entries where version_id = p_id;
  else
    raise exception 'no publish check defined for dataset %', v.dataset;
  end if;
  if n <> v.declared_rows then
    raise exception 'version % has % rows but declared %; not published', p_id, n, v.declared_rows;
  end if;
  update ref.dataset_versions set status = 'retired' where dataset = v.dataset and status = 'published';
  update ref.dataset_versions set status = 'published', published_at = now() where id = p_id;
end $$;

-- Supabase custom access token hook: puts the caller's roles into the JWT as "roles" for the GxP gateway.
-- Data access never trusts this claim; RLS reads ref.user_roles directly, so revocation is immediate.
create function ref.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare r jsonb;
begin
  select coalesce(jsonb_agg(role order by role), '[]'::jsonb) into r
  from ref.user_roles where user_id = (event ->> 'user_id')::uuid;
  return jsonb_set(event, '{claims}', coalesce(event -> 'claims', '{}'::jsonb) || jsonb_build_object('roles', r));
end $$;

-- ---------------------------------------------------------------- row-level security
alter table ref.roles            enable row level security;
alter table ref.role_datasets    enable row level security;
alter table ref.user_roles       enable row level security;
alter table ref.role_audit       enable row level security;
alter table ref.dataset_versions enable row level security;
alter table ref.iid_entries      enable row level security;

create policy roles_read on ref.roles for select to authenticated using (true);
create policy role_datasets_read on ref.role_datasets for select to authenticated using (true);
create policy user_roles_own on ref.user_roles for select to authenticated
  using (user_id = (select auth.uid()));
-- role_audit: no policy for clients; only the owner (admin via SQL) reads it.

create policy versions_read on ref.dataset_versions for select to authenticated
  using (status = 'published' and (select ref.can_read(dataset)));
create policy iid_read on ref.iid_entries for select to authenticated
  using ((select ref.can_read('iid')) and version_id = (select ref.published_version('iid')));

create policy versions_ingest_read on ref.dataset_versions for select to ref_ingest using (true);
create policy versions_ingest_stage on ref.dataset_versions for insert to ref_ingest with check (status = 'staged');
create policy versions_ingest_discard on ref.dataset_versions for delete to ref_ingest using (status = 'staged');
create policy iid_ingest_read on ref.iid_entries for select to ref_ingest using (true);
create policy iid_ingest_stage on ref.iid_entries for insert to ref_ingest
  with check (exists (select 1 from ref.dataset_versions v where v.id = version_id and v.status = 'staged'));
create policy iid_ingest_discard on ref.iid_entries for delete to ref_ingest
  using (exists (select 1 from ref.dataset_versions v where v.id = version_id and v.status = 'staged'));

-- ---------------------------------------------------------------- api (the only exposed schema)
create function api.resolve_ingredient(q text, max_results integer default 10)
returns table (ingredient_name text, unii text, cas_number text, matched_on text, score real, entries integer)
language sql stable security invoker set search_path = '' as $$
  with qn as (select upper(btrim(q)) as q, regexp_replace(q, '[^0-9]', '', 'g') as digits),
  names as (
    select e.ingredient_name, max(e.unii) as unii, max(e.cas_number) as cas_number, count(*)::integer as entries
    from ref.iid_entries e
    where e.version_id = ref.published_version('iid')
    group by e.ingredient_name
  ),
  ranked as (
    select n.*,
      case when n.ingredient_name = qn.q then 'exact_name'
           when n.unii = qn.q then 'unii'
           when qn.digits <> '' and replace(n.cas_number, '-', '') = qn.digits and qn.q ~ '^[0-9-]+$' then 'cas'
           when left(n.ingredient_name, length(qn.q)) = qn.q then 'prefix'
           else 'similar' end as matched_on,
      extensions.similarity(n.ingredient_name, qn.q) as sim
    from names n, qn
    where length(qn.q) >= 2
  )
  select r.ingredient_name, r.unii, r.cas_number, r.matched_on,
         (case r.matched_on when 'similar' then r.sim else 1.0 end)::real as score, r.entries
  from ranked r
  where r.matched_on <> 'similar' or r.sim >= 0.3
  order by (r.matched_on = 'exact_name') desc, (r.matched_on in ('unii', 'cas')) desc,
           (r.matched_on = 'prefix') desc, r.sim desc, r.ingredient_name
  limit greatest(1, least(coalesce(max_results, 10), 50))
$$;

create function api.resolve_dosage_form(q text, route text default null, max_results integer default 10)
returns table (dosage_form text, matched_on text, score real, entries integer)
language sql stable security invoker set search_path = '' as $$
  with qn as (select upper(btrim(q)) as q, upper(btrim(route)) as r),
  forms as (
    select e.dosage_form, count(*)::integer as entries
    from ref.iid_entries e, qn
    where e.version_id = ref.published_version('iid') and (qn.r is null or e.route = qn.r)
    group by e.dosage_form
  ),
  ranked as (
    select f.*,
      case when f.dosage_form = qn.q then 'exact'
           when left(f.dosage_form, length(qn.q)) = qn.q then 'prefix'
           else 'similar' end as matched_on,
      extensions.similarity(f.dosage_form, qn.q) as sim
    from forms f, qn
    where length(qn.q) >= 2
  )
  select r.dosage_form, r.matched_on, (case r.matched_on when 'similar' then r.sim else 1.0 end)::real, r.entries
  from ranked r
  where r.matched_on <> 'similar' or r.sim >= 0.3
  order by (r.matched_on = 'exact') desc, (r.matched_on = 'prefix') desc, r.sim desc, r.dosage_form
  limit greatest(1, least(coalesce(max_results, 10), 50))
$$;

create function api.iid_rows(ingredient text, route text default null, dosage_form text default null)
returns table (row_no integer, ingredient_name text, route text, dosage_form text, cas_number text, unii text,
               potency_amount numeric, potency_unit text, max_daily_exposure numeric,
               max_daily_exposure_unit text, record_updated boolean)
language sql stable security invoker set search_path = '' as $$
  select e.row_no, e.ingredient_name, e.route, e.dosage_form, e.cas_number, e.unii, e.potency_amount,
         e.potency_unit, e.max_daily_exposure, e.max_daily_exposure_unit, e.record_updated
  from ref.iid_entries e
  where e.version_id = ref.published_version('iid')
    and e.ingredient_name = upper(btrim(ingredient))
    and (iid_rows.route is null or e.route = upper(btrim(iid_rows.route)))
    and (iid_rows.dosage_form is null or e.dosage_form = upper(btrim(iid_rows.dosage_form)))
  order by e.route, e.dosage_form, e.row_no
  limit 500
$$;

create function api.list_vocab(kind text, contains text default null)
returns table (value text, entries integer)
language plpgsql stable security invoker set search_path = '' as $$
begin
  if kind not in ('route', 'dosage_form', 'potency_unit') then
    raise exception 'kind must be route, dosage_form or potency_unit' using errcode = '22023';
  end if;
  return query
    select v.value, count(*)::integer
    from (select case kind when 'route' then e.route when 'dosage_form' then e.dosage_form
                           else e.potency_unit end as value
          from ref.iid_entries e where e.version_id = ref.published_version('iid')) v
    where v.value is not null
      and (contains is null or strpos(v.value, upper(btrim(contains))) > 0)
    group by v.value order by count(*) desc, v.value
    limit 500;
end $$;

create function api.dataset_status()
returns table (dataset text, label text, source_url text, source_sha256 text, row_count integer, published_at timestamptz)
language sql stable security invoker set search_path = '' as $$
  select v.dataset, v.label, v.source_url, v.source_sha256, v.declared_rows, v.published_at
  from ref.dataset_versions v where v.status = 'published' order by v.dataset
$$;

create function api.my_access()
returns table (role text, dataset text)
language sql stable security invoker set search_path = '' as $$
  select ur.role, rd.dataset from ref.user_roles ur join ref.role_datasets rd on rd.role = ur.role
  where ur.user_id = auth.uid() order by 1, 2
$$;

-- ---------------------------------------------------------------- privileges (least privilege)
revoke all on all tables in schema ref from public, anon, authenticated;
revoke execute on all functions in schema ref from public, anon, authenticated;
revoke execute on all functions in schema api from public, anon;

grant usage on schema ref to authenticated, ref_ingest, supabase_auth_admin;
grant usage on schema api to authenticated;
grant usage on schema extensions to authenticated;
grant select on ref.roles, ref.role_datasets, ref.user_roles, ref.dataset_versions, ref.iid_entries to authenticated;
grant execute on function ref.can_read(text), ref.published_version(text) to authenticated, ref_ingest;
grant execute on all functions in schema api to authenticated;

grant select, insert, delete on ref.dataset_versions, ref.iid_entries to ref_ingest;
grant execute on function ref.publish_version(bigint) to ref_ingest;

grant execute on function ref.custom_access_token_hook(jsonb) to supabase_auth_admin;
