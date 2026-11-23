# Reference data server (`ref`): design

## Problem
Pharma teams look up public FDA reference data by hand, every week: the Inactive Ingredient Database (IID),
product-specific guidances (PSGs), the Dissolution Methods Database and the Drug Master File list. None of
these is reachable by an AI agent through MCP. An agent answering from memory invents limits.

`ref` gives agents exact, versioned, cited lookups of that data, behind the same role-based access
control as the rest of OpenGxP. Phase 1 is the IID; the other datasets reuse the same pattern.

## Where it lives
Inside the OpenGxP repo, as a sub-server mounted next to `cmc`, `stab`, `sdtm` and `ind` (tools prefixed `ref_`):
- it reuses the result envelope (summary, findings, citations, provenance);
- the GxP gateway already enforces tool-level roles, e-signatures and the audit trail for any tool;
- users install one package. The data lives in Supabase, not in the repo, so data refreshes need no release.

## HLD
```
FDA download (quarterly) ─► ingest job (GitHub Actions, role ref_ingest)
                             parse → normalise → stage version → validate → publish (atomic)
                                                   │
                                     Supabase Postgres (schema ref, RLS on every table)
                                                   │  only the api schema is exposed (RPC functions)
Agent ─MCP─► GxP gateway ─► ref tools ─► PostgREST /rpc/* with the caller's own JWT
             tool-level RBAC            data-level RBAC (RLS reads the JWT 'roles' claim)
```

### Two layers of access control, one set of role names
| Layer | Enforced by | Decides |
|---|---|---|
| Tool | GxP gateway `policy.yaml` (`roles.<name>.allow` globs) | Which `ref_*` tools a role may call or even see |
| Data | Postgres row-level security + `ref.role_datasets` | Which datasets a role may read, on every query, whatever client is used |

Roles are assigned in `ref.user_roles` (by an admin, change-controlled). A Supabase *custom access token
hook* copies them into the JWT as `roles: [...]`. The gateway reads the same `roles` claim, so one assignment
drives both layers. If the gateway is bypassed, the database still refuses.

Default roles (data in `ref.role_datasets`, editable):

| Role | Datasets |
|---|---|
| `viewer` | iid |
| `formulation` | iid |
| `generics` | iid, psg, dissolution (when loaded) |
| `sourcing` | dmf (when loaded) |
| `admin` | all |

### Security rules
1. **No anonymous access.** `anon` and `PUBLIC` have no privileges on `ref` or `api`; EXECUTE on every `api`
   function is revoked from `PUBLIC` (Postgres grants it by default) and granted to `authenticated` only.
2. **Clients reach data only through `api` functions.** Only `api` is exposed by PostgREST; `ref` tables are not.
   Functions are `SECURITY INVOKER`, so RLS applies to the caller.
3. **RLS on every table.** Readers see rows only of *published* versions of datasets their roles allow.
   Users see only their own role assignments. No client role can insert, update or delete.
4. **Ingest uses its own role** (`ref_ingest`): it may stage new versions and publish them, nothing else. Never
   the `service_role` key. Published rows are immutable; old versions are retired, not deleted, so any
   past answer can be reproduced.
5. **Publish is a checked transaction.** Row count must equal the count declared at staging, otherwise it
   fails and the previous version stays live.
6. **Tokens:** HTTP mode uses OAuth with Supabase Auth (FastMCP `SupabaseProvider`); the caller's token is
   forwarded to PostgREST. Local stdio mode uses `opengxp-ref login`, which stores a refresh token in a
   0600 file and never logs it. The Supabase URL and the publishable (anon) key are not secrets; RLS
   is what protects the data.
7. **Audit:** every tool call is recorded by the gateway's hash-chained audit trail; each result carries the
   dataset version, source file hash and source URL.

## LLD

### Schema `ref`
| Table | Purpose |
|---|---|
| `dataset_versions` | One row per loaded source file: dataset, label (e.g. "October 2026"), source URL, sha256, declared row count, status `staged` / `published` / `retired`, timestamps |
| `iid_entries` | IID rows: version, row number, ingredient name, route, dosage form, CAS, UNII (null when FDA says NA), potency amount + unit, maximum daily exposure + unit, record-updated flag |
| `user_roles` | user ID → role, granted by, granted at |
| `role_datasets` | role → dataset |

### `api` functions (all `STABLE`, `SECURITY INVOKER`)
| Function | Returns |
|---|---|
| `resolve_ingredient(q, max_results)` | Distinct IID names (+ UNII, CAS) ranked: exact name → exact UNII/CAS → prefix → trigram similarity, with `matched_on` and score |
| `resolve_dosage_form(q, route)` | Distinct dosage forms (optionally for a route) ranked the same way |
| `iid_rows(ingredient, route, dosage_form)` | Exact rows from the published IID version |
| `list_vocab(kind, contains)` | routes, dosage forms or potency units with counts |
| `dataset_status()` | Published versions the caller may read |

### Python (`src/opengxp/ref/`)
| Module | Responsibility |
|---|---|
| `iid_ingest.py` | Parse the FDA xlsx, normalise (`NA` → null, numbers, CAS dashes with check digit), hash the file; load with batched inserts (Postgres refuses `COPY` into tables with row-level security) and publish, as `ref_ingest` |
| `client.py` | PostgREST RPC client (httpx). Bearer = caller's token; maps 401/403 to a clear access error |
| `session.py` | Local login: password grant (typed by the user), refresh, 0600 token file |
| `excipients.py` | Pure verdict logic: unit normalisation (`mg/5ml` → mg/mL), compare per unit and per day, findings |
| `server.py` | FastMCP `ref` tools: `ref_resolve_ingredient`, `ref_resolve_dosage_form`, `ref_check_excipients`, `ref_excipient_precedents`, `ref_list_vocab`, `ref_dataset_status` |

### Verdict rules for `ref_check_excipients`
- The lookup is exact (ingredient name as resolved, route, dosage form). Fuzzy matching happens only in the resolve tools.
- Compare against **potency per unit** only when units are the same after normalisation; otherwise say "not comparable".
- Compare **daily exposure** only when `units_per_day` is given and FDA lists a maximum daily exposure (mg).
- `ADJ PH` rows (pH adjusters) have no amount: report as precedent without a limit.
- Verdicts: `within_precedent`, `above_precedent`, `not_comparable`, `no_precedent` (no row for that route and form).
- Above precedent is a finding, not a ban: FDA allows higher levels with justification.

### Tests
- Unit: parsing on real IID rows (public domain fixture), unit normalisation, every verdict branch.
- Client: httpx mock transport (headers, error mapping).
- Database (skipped unless `OPENGXP_TEST_PG` is set): migrations on Postgres with a minimal Supabase stand-in
  (`auth.jwt()`, roles `anon` / `authenticated` / `supabase_auth_admin`); asserts anon is refused, a user
  without a role sees nothing, a `formulation` user reads only published IID rows, users cannot write or read
  others' roles, staged versions stay invisible, and publish refuses a row-count mismatch.

### What the real data showed (October 2026 release)
- 9,076 rows, 1,795 ingredient names, 60 routes, 106 dosage forms; every CAS number passes its check digit.
- A row holds a per-unit limit (3,659), a daily limit in mg (4,211) or neither (1,206), never both. Daily limits
  need `units_per_day`; the tool says so instead of guessing.
- FDA names are literal: `PEG 400` does not resolve to `POLYETHYLENE GLYCOL 400` (trigram similarity suggests
  `PEG-40 CASTOR OIL`, marked `similar`, so the agent must ask). A curated synonym table (FDA GSRS names) is next.
- Full load ~1 s; exact lookups < 1 ms, name resolution ~10-20 ms on Postgres 17.

### Not in phase 1
Synonym table, PSG extraction, dissolution, DMF/CEP, vector search, per-user rate limiting (gateway or edge function).

## Setting up (Supabase)
1. **Use a dedicated Supabase project** for OpenGxP. Supabase tokens are valid for the whole project (no
   per-resource audience), so a shared project would let tokens from other apps reach this server.
2. Apply the migration: `supabase db push`, or run `supabase/migrations/*_ref_schema.sql` in the SQL editor.
3. **API settings → Exposed schemas:** add `api`. Do **not** expose `ref`.
4. **Authentication → Hooks → Custom access token:** select `ref.custom_access_token_hook`.
5. Create the ingest login (SQL editor), store its connection string as the `REF_INGEST_DSN` secret of the
   `reference-data` GitHub environment, and protect that environment with required reviewers:
   `create role ingest_bot login password '<generated>' in role ref_ingest;`
6. Load data: run the *Load FDA reference data* workflow with the FDA file link and label.
7. Assign roles (admin, change-controlled): `insert into ref.user_roles (user_id, role) values ('<auth user id>', 'formulation');`
   Removing the row revokes data access immediately; every change is written to `ref.role_audit`.
8. Clients set `OPENGXP_REF_URL` and `OPENGXP_REF_KEY` (the publishable key; secret / service_role keys are refused).
   - Local: `opengxp-ref login --email you@company.com`, then `uvx opengxp` as usual.
   - Remote: `opengxp-gateway serve --transport http --auth supabase --public-url https://<host> --policy policy.yaml`.
     Users sign in with Supabase; the gateway applies tool-level roles from the token's `roles` claim and the
     ref tools forward the same token, so row-level security applies too.
