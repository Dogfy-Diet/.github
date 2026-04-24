# Dogfy-Diet Reusable Workflows

Reusable GitHub Actions workflows shared across all Dogfy-Diet repositories. Any repo in the org can call these via `uses: Dogfy-Diet/.github/.github/workflows/<workflow>@main`.

These workflows implement Dogfy's **trunk-based delivery model** — build once on merge to main, deploy immediately to staging, promote the exact same image digest to production on demand, roll back by traffic shift.

```
┌─────────────────────┐           ┌─────────────────────┐
│ PR opened / sync    │           │ PR closed           │
└──────────┬──────────┘           └──────────┬──────────┘
           │                                 │
           ▼                                 ▼
   ┌───────────────┐                 ┌───────────────┐
   │ validate.yml  │                 │ cleanup-      │
   │ preview-*.yml │                 │ preview.yml   │
   └───────┬───────┘                 └───────────────┘
           │
           ▼
    (merge to main)
           │
           ▼
   ┌──────────────────┐
   │ deploy-cloud-    │  ← stg auto-deploy, tag `stg-latest`, smoke test
   │ run.yml          │
   └──────────────────┘
           │
           ▼
   ┌──────────────────┐
   │ promote-cloud-   │  ← manual trigger. Retag stg-latest → pro-latest,
   │ run.yml          │     deploy same digest to pro (zero rebuild)
   └──────────────────┘
           │
           ▼
   ┌──────────────────┐
   │ rollback-cloud-  │  ← manual trigger if prod breaks. Traffic shift
   │ run.yml          │     to previous revision in ~2 seconds
   └──────────────────┘

 cron/weekly ─────────▶  cleanup-ar-tags.yml  (prune stale sha-* tags)
```

> **SPAs**: pair these workflows with [`Dogfy-Diet/spa-base`](https://github.com/Dogfy-Diet/spa-base) as the base Docker image. That takes care of nginx, runtime config, cache headers, compression, and security headers once for every SPA — your `Dockerfile` becomes ~10 lines.

## Workflows at a glance

| Workflow | Trigger | Purpose |
|---|---|---|
| [`validate.yml`](.github/workflows/validate.yml) | PR | lint + typecheck + test + build + audit (parallel) |
| [`preview-cloud-run.yml`](.github/workflows/preview-cloud-run.yml) | PR | ephemeral Cloud Run tag `pr-N` with PR comment |
| [`cleanup-preview.yml`](.github/workflows/cleanup-preview.yml) | PR closed | remove traffic tag + delete AR `pr-N` |
| [`deploy-cloud-run.yml`](.github/workflows/deploy-cloud-run.yml) | push to main (or `workflow_dispatch`) | build + push + deploy stg + smoke + CDN invalidate |
| [`promote-cloud-run.yml`](.github/workflows/promote-cloud-run.yml) | `workflow_dispatch` | retag stg-latest → pro-latest/pro-previous + deploy pro (zero rebuild) |
| [`rollback-cloud-run.yml`](.github/workflows/rollback-cloud-run.yml) | `workflow_dispatch` | traffic shift or specific-tag redeploy |
| [`release.yml`](.github/workflows/release.yml) | push to main | semantic-release tagging + GitHub Release |
| [`cleanup-ar-tags.yml`](.github/workflows/cleanup-ar-tags.yml) | cron weekly | prune stale `sha-*` AR tags |

---

## Workflows

### [`deploy-cloud-run.yml`](.github/workflows/deploy-cloud-run.yml)

Build + push + deploy to staging. Designed for `push: branches: [main]` with a `paths:` filter.

**Features:**
- Build cache in Artifact Registry (`{service}:buildcache`), not GHA cache — no 10 GB limit, cross-branch sharing.
- Reuses the image from the closing PR's preview if available (avoids double-building).
- Smoke test with 3 retries after deploy.
- Auto-rollback: on smoke-test failure, traffic shifts back to the saved prior revision.
- CDN cache invalidation (`cdn_host` + env-scoped `CDN_URL_MAP`).
- Atomic tag moves: `stg-latest` is only moved **after** smoke test passes.
- Concurrency lock per `{service, environment}`.

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `environment` | yes | — | GitHub environment (`stg`, `pro`, etc.) |
| `service` | yes | — | Cloud Run service name |
| `region` | no | `europe-west1` | GCP region |
| `dockerfile` | no | `Dockerfile` | Path to Dockerfile |
| `context` | no | `.` | Docker build context |
| `build_args` | no | `""` | Docker build args (multiline) |
| `node_version` | no | `""` | Node version for optional runner-side prebuild |
| `install_command` | no | `""` | Install command for prebuild (e.g. `npm ci`) |
| `build_command` | no | `""` | Build command for prebuild (e.g. `npm run build`) |
| `build_cache_path` | no | `""` | Directory to cache across runs (e.g. Nuxt's `.nuxt`) |
| `smoke_test_path` | no | `""` | Health check path. Empty = skip |
| `cdn_host` | no | `""` | Host for CDN invalidation. Requires `vars.CDN_URL_MAP` |
| `sa_email` | no | `""` | Deployer SA override. For monorepos |
| `submodules` | no | `"false"` | Git submodule fetch |

**Secrets:** `submodules_token` (optional, PAT for private submodules).

**Outputs:** `image`, `digest`, `url`.

---

### [`promote-cloud-run.yml`](.github/workflows/promote-cloud-run.yml)

**Zero-rebuild promote** from staging to production. Triggered manually via `workflow_dispatch`. Takes a version reference, resolves it to an **immutable digest** up-front, deploys that digest to production, then (on success) moves the tags.

**Digest-first flow:**

```
1. Resolve user input → source tag → source digest            (once, up-front)
2. Capture current pro-latest digest                          (save for pro-previous)
3. Capture currently-serving Cloud Run revision               (save for auto-rollback)
4. Deploy Cloud Run by digest                                 (tags not touched)
5. Smoke test
6. On success:  move pro-latest, pro-previous, semver tags.
   On failure:  shift traffic back to saved revision; tags untouched.
```

Why digest-first: between "read stg-latest" and "write pro-latest", a concurrent push to main can move stg-latest. Resolving to a digest up-front eliminates that race.

**Accepted `version` inputs:**
- Commit hash (`b075fa6` or full 40-char) → resolved to `sha-b075fa6`
- Semver (`v1.4.0` or `1.4.0`) → source is `stg-latest`, tagged `v1.4.0` on success
- AR tag (`sha-b075fa6`, `stg-latest`, `pro-previous`) → used as-is

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `service` | yes | — | Cloud Run service name |
| `version` | yes | — | Version to promote (see above) |
| `environment` | no | `production` | GitHub environment (use `pro` if that's your naming) |
| `region` | no | `europe-west1` | — |
| `smoke_test_path` | no | `""` | Health check path |
| `cdn_host` | no | `""` | CDN invalidation host (same rule as deploy) |
| `sa_email` | no | `""` | Deployer SA override |

**Caller example:**

```yaml
# promote-myapp.yml
name: Promote MyApp
on:
  workflow_dispatch:
    inputs:
      version:
        description: "Tag/sha/semver to promote. Default: current stg-latest"
        required: false
        default: stg-latest

permissions:
  contents: read
  id-token: write
  deployments: write

jobs:
  promote:
    uses: Dogfy-Diet/.github/.github/workflows/promote-cloud-run.yml@main
    with:
      service: dogfy-myapp
      version: ${{ inputs.version }}
      environment: pro
      smoke_test_path: /health
      cdn_host: myapp.dogfydiet.com
```

---

### [`rollback-cloud-run.yml`](.github/workflows/rollback-cloud-run.yml)

Two modes for rolling back a production deploy:

| Mode | What it does | Use when |
|---|---|---|
| `previous` (default) | Traffic shift to the most recent non-current revision (~2 s) | Latest deploy is broken and the previous revision is still good |
| `specific` | Deploy a specific AR tag (e.g. `pro-previous`, `sha-abc1234`, `v1.3.2`) | You need to jump further back, or the previous revision was also broken |

Falls back to the `pro-previous` AR tag if no suitable revision is found locally.

Protected by a GitHub environment (approval gate).

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `service` | yes | — | Cloud Run service name |
| `environment` | no | `production` | GitHub environment |
| `mode` | no | `previous` | `previous` or `specific` |
| `version` | no (req if mode=specific) | `""` | Tag/sha/semver |
| `region` | no | `europe-west1` | — |
| `smoke_test_path` | no | `""` | — |
| `cdn_host` | no | `""` | — |
| `sa_email` | no | `""` | Deployer SA override |

**Caller example:**

```yaml
# rollback-myapp.yml
name: Rollback MyApp
on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [previous, specific]
        default: previous
      version:
        required: false
        default: pro-previous

permissions:
  contents: read
  id-token: write
  deployments: write

jobs:
  rollback:
    uses: Dogfy-Diet/.github/.github/workflows/rollback-cloud-run.yml@main
    with:
      service: dogfy-myapp
      environment: pro
      mode: ${{ inputs.mode }}
      version: ${{ inputs.version }}
      smoke_test_path: /health
      cdn_host: myapp.dogfydiet.com
```

---

### [`validate.yml`](.github/workflows/validate.yml)

Runs lint, typecheck, test, build, and security audit **in parallel**. Each check is optional (pass an empty string to skip) and can be marked strict or non-strict.

```
┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
│    lint    │  │ typecheck  │  │    test    │  │   build    │  │   audit    │
└────────────┘  └────────────┘  └────────────┘  └────────────┘  └────────────┘
       │               │               │              │               │
       └───────────────┴───────────────┴──────────────┴───────────────┘
                                  parallel
                                     │
                              ┌──────┴──────┐
                              │   summary   │
                              └─────────────┘
```

**Inputs:**

| Input | Default | Description |
|---|---|---|
| `node_version` | `22` | Node.js version |
| `install_command` | `npm ci` | Package install |
| `lint_command` | `npm run lint` | Lint. Empty = skip |
| `typecheck_command` | `""` | Typecheck (e.g. `npx vue-tsc --noEmit`). Empty = skip |
| `test_command` | `npm test` | Tests. Empty = skip |
| `build_command` | `npm run build` | Build. Empty = skip |
| `audit_command` | `npm audit --audit-level=moderate` | Audit. Empty = skip |
| `setup_command` | `""` | Pre-check setup (e.g. `npx nuxt prepare`) |
| `non_strict_checks` | `"audit"` | Comma-list of checks that don't block (any of: `lint`, `typecheck`, `test`, `build`, `audit`) |
| `env_vars` | `""` | Multiline dummy env vars for checks that read them |
| `cache_service_name` | `""` | Cache key namespace (helpful in monorepos) |
| `submodules` | `"false"` | — |

**Strict vs non-strict:**

| | Strict (default) | Non-strict |
|---|---|---|
| **Check passes** | Job green, status `success` | Job green, status `success` |
| **Check fails** | Job red, **blocks PR** | Job green, warning annotation, **does not block** |

A **summary job** runs after all checks and produces a results table; only checks that actually ran are shown.

---

### [`preview-cloud-run.yml`](.github/workflows/preview-cloud-run.yml)

Deploys an ephemeral PR preview using Cloud Run traffic tags. The preview gets a unique URL like `https://pr-42---service-abc.a.run.app` and receives **no production traffic**.

**Features:**
- Builds and pushes image tagged `pr-N`.
- Deploys with `--no-traffic` (stg main stays untouched).
- Health check on preview URL (optional).
- Auto-comments on the PR with the preview URL.
- Aggressive concurrency: cancels stale preview builds for the same PR.

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `service` | yes | — | Cloud Run service name |
| `region` | no | `europe-west1` | — |
| `dockerfile` | no | `Dockerfile` | — |
| `context` | no | `.` | — |
| `build_args` | no | `""` | — |
| `node_version` | no | `""` | — |
| `install_command` | no | `""` | — |
| `build_command` | no | `""` | — |
| `smoke_test_path` | no | `""` | Health check path on the preview URL |
| `sa_email` | no | `""` | — |
| `submodules` | no | `"false"` | — |

**Output:** `preview_url`.

**Prerequisite:** Cloud Run service must have `ingress = INGRESS_TRAFFIC_ALL` in Terraform (staging only) so the tagged URL is reachable from outside the LB.

---

### [`cleanup-preview.yml`](.github/workflows/cleanup-preview.yml)

Removes the Cloud Run traffic tag and the AR `pr-N` tag when a PR is closed. Without this, stale previews linger until the next staging deploy.

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `service` | yes | — | Cloud Run service name |
| `region` | no | `europe-west1` | — |
| `sa_email` | no | `""` | — |

**Trigger:** `on: pull_request: types: [closed]` (paired with same `paths` filter as preview-cloud-run to skip irrelevant PRs).

---

### [`release.yml`](.github/workflows/release.yml)

Automates semantic versioning, changelog, and GitHub Releases using [semantic-release](https://github.com/semantic-release/semantic-release). Analyzes conventional commits since the last tag to determine the version bump.

```
feat: add export button     → minor (1.x.0)
fix: correct date parsing   → patch (1.0.x)
feat!: redesign auth flow   → major (x.0.0)
chore: update deps          → no release
```

**Features:**
- Generates default config if repo has no `.releaserc*` file (zero config for most repos).
- Respects existing `.releaserc.json` / `release.config.js` if present.
- CHANGELOG.md + package.json version bump committed to main (optional).
- `[skip ci]` in release commit to avoid infinite loops.
- GitHub Release with auto-generated release notes.

**Inputs:**

| Input | Default | Description |
|---|---|---|
| `node_version` | `22` | — |
| `commit_changelog` | `true` | Commit CHANGELOG.md + version bump to main. Set `false` if branch protection blocks direct pushes |
| `extra_plugins` | `""` | Additional semantic-release plugins (space-separated npm packages) |

**Outputs:** `released` (boolean), `version` (e.g. `1.4.0`).

> ⚠ This reusable release workflow operates on the **repo root's** `package.json`. It does not support per-workspace scoping. For monorepos where different apps cut releases independently, you'd need either semantic-release-monorepo or a per-app release workflow in the caller repo.

---

### [`cleanup-ar-tags.yml`](.github/workflows/cleanup-ar-tags.yml)

Removes stale `sha-*` image tags from Artifact Registry and untagged images. Prevents unbounded tag accumulation from staging deploys.

**Protected tags (never deleted):** `stg-latest`, `pro-latest`, `pro-previous`, `v*.*.*`, `buildcache`.

**Not touched by this workflow:** `pr-N` tags — those are cleaned by `cleanup-preview.yml` when the PR closes.

**Inputs:**

| Input | Required | Default | Description |
|---|:-:|---|---|
| `service` | yes | — | Image name in AR (e.g. `dogfy-cust`) |
| `keep_last` | no | `10` | Number of recent `sha-*` tags to keep |
| `sa_email` | no | `""` | — |
| `region` | no | `europe-west1` | — |

---

## Docker Build & Tagging Strategy

### Tags

```
{service}:sha-abc1234     ← immutable, per commit (human-readable reference)
{service}:stg-latest      ← mutable, points to the image currently serving staging
{service}:pr-42           ← ephemeral, per PR (cleaned up on close)
{service}:pro-latest      ← mutable, points to the image currently serving production
{service}:pro-previous    ← mutable, saved right before pro-latest is overwritten (rollback target)
{service}:v1.4.0          ← immutable, per release (set by promote-cloud-run.yml)
{service}:buildcache      ← cache layers (not a runnable image)
```

### Digest-based deployment

Cloud Run deploys always use the **image digest** (e.g. `@sha256:abc123...`), not tags. Tags are mutable pointers; digests are immutable hashes. This guarantees the exact image that was built/promoted is what runs.

```
Build & Push  ──▶  tags: sha-abc1234, stg-latest
                   outputs: digest sha256:abc123...
                               │
                               ▼
Deploy       ──▶  --image={service}@sha256:abc123...   (digest, immutable)
```

### Registry cache

Build cache is stored in AR (`{service}:buildcache`) instead of GitHub Actions cache. Removes the 10 GB limit and enables cross-branch cache sharing.

### Tag lifecycle

| Tag | Created | Deleted / Rotated | By |
|---|---|---|---|
| `sha-*` | Every deploy build | Weekly cleanup beyond `keep_last=10` | `cleanup-ar-tags.yml` |
| `stg-latest` | Every staging deploy | Never deleted; moved to the new digest | `deploy-cloud-run.yml` |
| `pro-latest` | Every promote | Moved to the new digest | `promote-cloud-run.yml` |
| `pro-previous` | Every promote | Overwritten with the previous `pro-latest` before the new one lands | `promote-cloud-run.yml` |
| `v*.*.*` | Every semver promote | Never | `promote-cloud-run.yml` |
| `pr-*` | Every PR push | PR close | `cleanup-preview.yml` |
| `buildcache` | Every build | Never (overwritten) | `deploy-cloud-run.yml` |

---

## Usage examples

### SPAs — the standard Dogfy pattern

With [`spa-base`](https://github.com/Dogfy-Diet/spa-base) the app's `Dockerfile` is ~10 lines:

```dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY . .
RUN npm ci --workspace=@dogfy/<app> && npm run build --workspace=@dogfy/<app>

FROM europe-west1-docker.pkg.dev/dogfy-host/docker/spa-base:v1
COPY --from=builder /app/apps/<app>/dist /usr/share/nginx/html
```

And a minimal caller workflow for deploy:

```yaml
# deploy-myapp.yml
name: Deploy MyApp
on:
  push:
    branches: [main]
    paths:
      - "apps/myapp/**"
      - "packages/**"
      - "services/**"
      - "package*.json"
      - ".github/workflows/deploy-myapp.yml"
  workflow_dispatch:

permissions:
  contents: read
  id-token: write
  deployments: write

jobs:
  deploy:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: stg
      service: dogfy-myapp
      sa_email: myapp-deployer@dogfy-host.iam.gserviceaccount.com
      dockerfile: apps/myapp/Dockerfile
      smoke_test_path: /health
      cdn_host: myapp-mig.staging.dogfydiet.com
```

Full set of 7 caller workflows (validate / preview / deploy / promote / rollback / cleanup-preview / cleanup-ar-tags) for a standard SPA: see the `cust` app in [`web-apps-migration`](https://github.com/Dogfy-Diet/web-apps-migration/tree/main/.github/workflows) as a reference.

### Monorepo with multiple services (same repo, different services)

Each service uses a **different deployer SA** (one per service, all living in `dogfy-host`). Pass `sa_email` per workflow — `vars.SA_EMAIL` is a single value per GitHub environment.

```yaml
# deploy-backoffice.yml
jobs:
  deploy-stg:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: stg
      service: dogfy-backoffice
      sa_email: backoffice-deployer@dogfy-host.iam.gserviceaccount.com
      dockerfile: Dockerfile
      build_args: |
        APP=backoffice
      smoke_test_path: /health
      cdn_host: bo-mig.staging.dogfydiet.com
```

```yaml
# deploy-admin.yml — same repo, different service + SA
jobs:
  deploy-stg:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: stg
      service: dogfy-admin
      sa_email: admin-deployer@dogfy-host.iam.gserviceaccount.com
      dockerfile: Dockerfile
      build_args: |
        APP=admin
      smoke_test_path: /health
      cdn_host: admin-mig.staging.dogfydiet.com
```

### PR validation + preview + cleanup

```yaml
# validate.yml (shared across all apps in the monorepo)
name: Validate
on:
  pull_request:
    branches: [main]
    paths:
      - "apps/**"
      - "packages/**"
      - "services/**"

permissions:
  contents: read
  id-token: write

jobs:
  validate:
    uses: Dogfy-Diet/.github/.github/workflows/validate.yml@main
    with:
      install_command: npm ci
      typecheck_command: cd apps/myapp && npm run typecheck
      non_strict_checks: "audit"
```

```yaml
# preview-myapp.yml (per app)
name: Preview MyApp
on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - "apps/myapp/**"
      - "packages/**"
      - "apps/myapp/Dockerfile"

permissions:
  contents: read
  id-token: write
  pull-requests: write

jobs:
  preview:
    uses: Dogfy-Diet/.github/.github/workflows/preview-cloud-run.yml@main
    with:
      service: dogfy-myapp
      sa_email: myapp-deployer@dogfy-host.iam.gserviceaccount.com
      dockerfile: apps/myapp/Dockerfile
      smoke_test_path: /health
```

```yaml
# cleanup-preview-myapp.yml (per app)
name: Cleanup Preview MyApp
on:
  pull_request:
    types: [closed]
    paths:  # same filter as preview-myapp.yml!
      - "apps/myapp/**"
      - "packages/**"
      - "apps/myapp/Dockerfile"

permissions:
  contents: read
  id-token: write

jobs:
  cleanup:
    uses: Dogfy-Diet/.github/.github/workflows/cleanup-preview.yml@main
    with:
      service: dogfy-myapp
      sa_email: myapp-deployer@dogfy-host.iam.gserviceaccount.com
```

### Promote + rollback

```yaml
# promote-myapp.yml
name: Promote MyApp
on:
  workflow_dispatch:
    inputs:
      version:
        description: "Tag/sha/semver. Default: stg-latest"
        required: false
        default: stg-latest

permissions:
  contents: read
  id-token: write
  deployments: write

jobs:
  promote:
    uses: Dogfy-Diet/.github/.github/workflows/promote-cloud-run.yml@main
    with:
      service: dogfy-myapp
      version: ${{ inputs.version }}
      environment: pro
      smoke_test_path: /health
      cdn_host: myapp.dogfydiet.com
```

```yaml
# rollback-myapp.yml
name: Rollback MyApp
on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [previous, specific]
        default: previous
      version:
        required: false
        default: pro-previous

permissions:
  contents: read
  id-token: write
  deployments: write

jobs:
  rollback:
    uses: Dogfy-Diet/.github/.github/workflows/rollback-cloud-run.yml@main
    with:
      service: dogfy-myapp
      environment: pro
      mode: ${{ inputs.mode }}
      version: ${{ inputs.version }}
      smoke_test_path: /health
      cdn_host: myapp.dogfydiet.com
```

### Weekly AR tag prune

```yaml
# cleanup-ar-tags-myapp.yml
name: Cleanup AR tags MyApp
on:
  schedule:
    - cron: "0 3 * * 1"  # Mondays 03:00 UTC
  workflow_dispatch: {}

permissions:
  contents: read
  id-token: write

jobs:
  cleanup:
    uses: Dogfy-Diet/.github/.github/workflows/cleanup-ar-tags.yml@main
    with:
      service: dogfy-myapp
      keep_last: 10
      sa_email: myapp-deployer@dogfy-host.iam.gserviceaccount.com
```

---

## Repo setup guide

Configure the following in **GitHub Settings → Secrets and variables → Actions** on each repo that consumes these workflows.

### Repository variables

| Variable | Example | Used by |
|---|---|---|
| `AR_REPO` | `europe-west1-docker.pkg.dev/dogfy-host/docker` | deploy, preview, cleanup-preview, cleanup-ar-tags |

### Environments

Create one environment per deployment target. Variables are **scoped** so `CDN_URL_MAP`, `GCP_PROJECT`, etc. can differ per env.

#### `stg` environment

| Variable | Example |
|---|---|
| `GCP_PROJECT` | `dogfy-platform-stg` |
| `SA_EMAIL` | `{service}-deployer@dogfy-host.iam.gserviceaccount.com` (or leave empty and pass `sa_email` input per workflow) |
| `WIF_PROVIDER` | `projects/454974353730/locations/global/workloadIdentityPools/github-actions-pool/providers/github-oidc` |
| `CDN_URL_MAP` | `platform-stg-lb-urlmap` (only if CDN used) |

No protection rules needed.

#### `pro` environment

| Variable | Example |
|---|---|
| `GCP_PROJECT` | `dogfy-platform-pro` |
| `SA_EMAIL` | same SA as stg (shared deployer pattern) |
| `WIF_PROVIDER` | same WIF pool |
| `CDN_URL_MAP` | `platform-pro-lb-urlmap` |

Enable **Required reviewers** as protection rule on `pro` for promote/rollback.

### Quick reference: variable scope

```
Repository (shared)
├── AR_REPO = europe-west1-docker.pkg.dev/dogfy-host/docker
│
├── Environment: stg
│   ├── GCP_PROJECT  = dogfy-platform-stg
│   ├── SA_EMAIL     = {service}-deployer@dogfy-host.iam.gsa.com   ← shared deployer in host
│   ├── WIF_PROVIDER = projects/454974353730/.../github-oidc
│   └── CDN_URL_MAP  = platform-stg-lb-urlmap                     (optional)
│
└── Environment: pro
    ├── GCP_PROJECT  = dogfy-platform-pro
    ├── SA_EMAIL     = same as stg
    ├── WIF_PROVIDER = same as stg
    └── CDN_URL_MAP  = platform-pro-lb-urlmap                      (optional)
```

### Caller workflow permissions

Any caller workflow **must** declare permissions explicitly. GitHub requires this for `id-token: write` (WIF needs it).

```yaml
permissions:
  contents: read
  id-token: write
  pull-requests: write   # only for preview-cloud-run
  deployments: write     # only for deploy / promote / rollback
```

If the caller doesn't declare `id-token: write`, WIF auth fails silently with a 403.

---

## Infrastructure prerequisites

All workflows use **Workload Identity Federation** (WIF) — no service account keys.

For each service, Terraform (in [`Dogfy-Diet/infrastructure`](https://github.com/Dogfy-Diet/infrastructure)) creates:

- **Runtime SA** (`{service}-runtime@{platform-env}.iam`) — used by Cloud Run at runtime.
- **Shared deployer SA** (`{service}-deployer@dogfy-host.iam`) — used by GitHub Actions via WIF. One SA deploys to both stg and pro.
- **WIF binding** — links the GitHub repo to the deployer SA.

The deployer SA gets:
- `roles/artifactregistry.repoAdmin` on `dogfy-host` — push + retag images.
- `roles/run.developer` + `roles/iam.serviceAccountUser` on each target project — deploy to Cloud Run.
- `roles/compute.loadBalancerAdmin` on each target project — CDN cache invalidation (for SPAs with CDN).

All Cloud Run config (env vars, secrets, scaling, networking) is owned by Terraform. CI/CD only deploys images via `gcloud run deploy --image`.

## Source of truth

These workflows live in this repo. Reference copies are also kept in the infrastructure repo at `docs/workflows/reusable/` alongside the Terraform modules that provision the GCP resources they depend on.
