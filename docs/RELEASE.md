# Branches, environments and releases

```
feature/* ──PR──► develop ──PR──► staging ──PR──► main ──tag v*──► PyPI
                    │                │              │
                 CI tests      staging Supabase   production Supabase
                 (local DB)    (automatic)        (required reviewers)
```

| Branch | Purpose | What happens on merge |
|---|---|---|
| `feature/*`, `fix/*` | One change each | CI on the PR (unit tests, Postgres access-control tests) |
| `develop` | Integration of finished features | CI |
| `staging` | Release candidate; QA checks it here | CI; new migrations applied to the **staging** Supabase project; FDA data loaded here first |
| `main` | What is released and running in production | CI; migrations applied to **production** after a required reviewer approves; a `v*` tag publishes to PyPI |
| `hotfix/*` | Urgent production fix | PR straight to `main` (or `staging`), then merge `main` back into `staging` and `develop` |

Rules:
- Code moves one way. The *Promotion guard* check fails a PR into `staging` unless it comes from `develop`
  (or `hotfix/*`), and a PR into `main` unless it comes from `staging` (or `hotfix/*`).
- Releases are tagged on `main` only; the release workflow refuses a tag that is not on `main`.
- Promote with a merge commit (not squash), so `develop`, `staging` and `main` share history and the next
  promotion has no false conflicts.

## Promoting
```bash
gh pr create --base staging --head develop --title "Promote develop to staging"
# QA checks staging (tools against the staging Supabase project, test evidence from CI)
gh pr create --base main --head staging --title "Release v0.5.0"
# after merge: bump version in pyproject.toml on the release PR, then
git tag v0.5.0 origin/main && git push origin v0.5.0
```

## One-time setup (repository owner)
1. **Environments** (Settings → Environments):
   - `staging`: secret `SUPABASE_DB_URL` (staging project, migration owner) and `REF_INGEST_DSN` (staging ingest login).
   - `production`: the same two secrets for the production project, **required reviewers** (QA), and
     deployment branches limited to `main`.
   - `pypi`: already used by the release workflow.
2. **Supabase:** two projects, `opengxp-staging` and `opengxp-prod` (setup steps per project in
   [REFDATA.md](REFDATA.md#setting-up-supabase)). Development uses local Postgres in Docker.
3. **Branch protection** for `develop`, `staging` and `main`: pull requests required, CI must pass,
   no force pushes; for `staging` and `main` also the *Promotion guard* check.

Until the secrets exist, the deploy workflow reports "nothing deployed" instead of failing.

## Rollback
- Code: revert the merge on `main` (new PR from `hotfix/*`), never rewrite history.
- Database: migrations only move forward; write a new migration that undoes the change, and promote it
  the same way. Reference data: published releases are immutable, and loading the previous FDA file again is
  refused as a duplicate, so a bad release is fixed by loading the corrected file as a new version.
