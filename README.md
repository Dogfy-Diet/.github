# Dogfy-Diet Reusable Workflows

Reusable GitHub Actions workflows shared across all Dogfy-Diet repositories. Any repo in the org can call these via `uses: Dogfy-Diet/.github/.github/workflows/<workflow>@main`.

## Workflows

### [`deploy-cloud-run.yml`](.github/workflows/deploy-cloud-run.yml)

Builds, pushes to Artifact Registry, and deploys to Cloud Run. Two modes:

| Mode | Trigger | What happens |
|------|---------|-------------|
| **Staging** | `version` empty | Checkout → build Docker image → push to AR → deploy |
| **Production** | `version` set (e.g. `v1.4.0`) | Retag `stg-latest` in AR → deploy (zero rebuild) |

**Features:**
- Docker layer caching (GitHub Actions cache)
- Smoke test with 3 retries after deploy
- Auto-rollback to previous revision if smoke test fails
- CDN cache invalidation (optional, via `cdn_host` input)
- Stale PR preview tag cleanup (staging only)
- Concurrency control per service/environment

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `environment` | yes | — | GitHub environment (`stg` or `production`) |
| `service` | yes | — | Cloud Run service name (must match Terraform) |
| `version` | no | `""` | Semver tag for production promote. Empty = build from source |
| `region` | no | `europe-west1` | GCP region |
| `dockerfile` | no | `Dockerfile` | Path to Dockerfile |
| `context` | no | `.` | Docker build context |
| `build_args` | no | `""` | Docker build args (multiline) |
| `node_version` | no | `""` | Node version for optional prebuild step |
| `install_command` | no | `""` | Install command for prebuild (e.g. `npm ci`) |
| `build_command` | no | `""` | Build command for prebuild (e.g. `npm run build`) |
| `smoke_test_path` | no | `""` | Health check path (e.g. `/health`). Empty = skip |
| `cdn_host` | no | `""` | Host for CDN invalidation. Requires `CDN_URL_MAP` env variable |

**Outputs:** `image`, `url`

**Required GitHub variables:**

| Scope | Variable | Example |
|-------|----------|---------|
| Repository | `AR_REPO` | `europe-west1-docker.pkg.dev/dogfy-host/docker` |
| Environment | `GCP_PROJECT` | `dogfy-platform-stg` |
| Environment | `SA_EMAIL` | `backoffice-deployer@dogfy-platform-stg.iam.gserviceaccount.com` |
| Environment | `WIF_PROVIDER` | `projects/454974353730/locations/global/.../github-oidc` |
| Environment | `CDN_URL_MAP` | `platform-stg-lb-urlmap` (only if using `cdn_host`) |

---

### [`validate.yml`](.github/workflows/validate.yml)

Runs lint, typecheck, test, build, and security audit **in parallel**. Each step is optional — pass an empty string to skip it.

```
┌───────┐  ┌───────────┐  ┌──────┐  ┌───────┐  ┌───────┐
│ lint  │  │ typecheck  │  │ test │  │ build │  │ audit │
└───────┘  └───────────┘  └──────┘  └───────┘  └───────┘
     ▲           ▲            ▲          ▲          ▲
     └───────────┴────────────┴──────────┴──────────┘
                         parallel
```

**Inputs:**

| Input | Default | Description |
|-------|---------|-------------|
| `node_version` | `22` | Node.js version |
| `install_command` | `npm ci` | Package install |
| `lint_command` | `npm run lint` | Lint command. Empty = skip |
| `typecheck_command` | `""` | Typecheck (e.g. `npx vue-tsc --noEmit`). Empty = skip |
| `test_command` | `npm test` | Test command. Empty = skip |
| `build_command` | `npm run build` | Build command. Empty = skip |
| `audit_command` | `npm audit --audit-level=moderate` | Security audit. Empty = skip |
| `setup_command` | `""` | Post-install prep (e.g. `npx nuxt prepare`). Runs before all jobs |

> The `audit` job uses `continue-on-error: true` — it reports vulnerabilities but doesn't block the PR.

---

### [`preview-cloud-run.yml`](.github/workflows/preview-cloud-run.yml)

Deploys an ephemeral PR preview using Cloud Run traffic tags. The preview gets a unique URL like `https://pr-42---service.a.run.app` and receives **no production traffic**.

**Features:**
- Builds and pushes image tagged `pr-N`
- Deploys with `--no-traffic` (isolated from staging)
- Health check on preview URL (optional)
- Auto-comments on the PR with the preview URL
- Aggressive concurrency: cancels stale preview builds for the same PR

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `service` | yes | — | Cloud Run service name |
| `region` | no | `europe-west1` | GCP region |
| `dockerfile` | no | `Dockerfile` | Dockerfile path |
| `context` | no | `.` | Docker build context |
| `build_args` | no | `""` | Docker build args |
| `node_version` | no | `""` | Node version for prebuild |
| `install_command` | no | `""` | Install command for prebuild |
| `build_command` | no | `""` | Build command for prebuild |
| `smoke_test_path` | no | `""` | Health check path for preview |

**Output:** `preview_url`

**Prerequisite:** Cloud Run service must have `ingress = INGRESS_TRAFFIC_ALL` in Terraform (staging only) so the tagged URL is reachable.

---

### [`cleanup-preview.yml`](.github/workflows/cleanup-preview.yml)

Removes the Cloud Run traffic tag and AR image tag when a PR is closed. Without this, stale previews linger until the next staging deploy.

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `service` | yes | — | Cloud Run service name |
| `region` | no | `europe-west1` | GCP region |

**Trigger:** Call this from `on: pull_request: types: [closed]` in your caller workflow.

---

### [`rollback-cloud-run.yml`](.github/workflows/rollback-cloud-run.yml)

Manual rollback to a previous version. Protected by the `production` GitHub environment (approval gate).

**Features:**
- Validates version tag exists in AR before deploying
- Smoke test with 3 retries (same as deploy)
- CDN cache invalidation (optional)
- Summary with actor and image info

**Inputs:**

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `service` | yes | — | Cloud Run service name |
| `version` | yes | — | Version tag to rollback to (e.g. `v1.3.2`) |
| `region` | no | `europe-west1` | GCP region |
| `smoke_test_path` | no | `""` | Health check path |
| `cdn_host` | no | `""` | Host for CDN invalidation |

---

## Usage Examples

### Minimal: deploy on push to main

```yaml
name: Deploy
on:
  push:
    branches: [main]

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: stg
      service: dogfy-my-service
      smoke_test_path: /health
```

### SPA with CDN (monorepo)

```yaml
name: Deploy Backoffice
on:
  push:
    branches: [main]
    paths: ["apps/backoffice/**", "packages/**", "Dockerfile", "nginx.conf", "entrypoint.sh"]
  workflow_dispatch:

permissions:
  contents: read
  id-token: write

jobs:
  deploy-stg:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: stg
      service: dogfy-backoffice
      build_args: |
        APP=backoffice
      smoke_test_path: /health
      cdn_host: bo.staging.dogfydiet.com
```

### PR validation + preview + cleanup

```yaml
name: PR
on:
  pull_request:
    types: [opened, synchronize, reopened, closed]

permissions:
  contents: read
  id-token: write
  pull-requests: write

jobs:
  validate:
    if: github.event.action != 'closed'
    uses: Dogfy-Diet/.github/.github/workflows/validate.yml@main
    with:
      typecheck_command: "npx vue-tsc --noEmit"
      audit_command: "npm audit --audit-level=high"

  preview:
    if: github.event.action != 'closed'
    needs: validate
    uses: Dogfy-Diet/.github/.github/workflows/preview-cloud-run.yml@main
    with:
      service: dogfy-my-service
      smoke_test_path: /health

  cleanup:
    if: github.event.action == 'closed'
    uses: Dogfy-Diet/.github/.github/workflows/cleanup-preview.yml@main
    with:
      service: dogfy-my-service
```

### Production promote + rollback

```yaml
# deploy-production.yml
name: Deploy Production
on:
  release:
    types: [published]

permissions:
  contents: read
  id-token: write

jobs:
  deploy:
    uses: Dogfy-Diet/.github/.github/workflows/deploy-cloud-run.yml@main
    with:
      environment: production
      service: dogfy-my-service
      version: ${{ github.event.release.tag_name }}
      smoke_test_path: /health
```

```yaml
# rollback.yml
name: Rollback
on:
  workflow_dispatch:
    inputs:
      version:
        description: "Version to rollback to (e.g. v1.3.2)"
        required: true

permissions:
  contents: read
  id-token: write

jobs:
  rollback:
    uses: Dogfy-Diet/.github/.github/workflows/rollback-cloud-run.yml@main
    with:
      service: dogfy-my-service
      version: ${{ inputs.version }}
      smoke_test_path: /health
```

## Repo Setup Guide

When onboarding a new repo to use these workflows, configure the following in **GitHub Settings → Secrets and variables → Actions**.

### Repository Variables (Settings → Variables → New repository variable)

These are shared across all environments in the repo.

| Variable | Example | Used by |
|----------|---------|---------|
| `AR_REPO` | `europe-west1-docker.pkg.dev/dogfy-host/docker` | deploy, preview, cleanup |

### Environments (Settings → Environments)

Create one environment per deployment target. Each environment has its own variables.

#### Environment: `stg`

| Variable | Example | Used by |
|----------|---------|---------|
| `GCP_PROJECT` | `dogfy-platform-stg` | deploy, preview, cleanup, rollback |
| `SA_EMAIL` | `my-service-deployer@dogfy-platform-stg.iam.gserviceaccount.com` | deploy, preview, cleanup |
| `WIF_PROVIDER` | `projects/454974353730/locations/global/workloadIdentityPools/github-actions-pool/providers/github-oidc` | deploy, preview, cleanup |
| `CDN_URL_MAP` | `platform-stg-lb-urlmap` | deploy (only if service uses CDN) |

> No protection rules needed for staging.

#### Environment: `production`

| Variable | Example | Used by |
|----------|---------|---------|
| `GCP_PROJECT` | `dogfy-platform-pro` | deploy, rollback |
| `SA_EMAIL` | `my-service-deployer@dogfy-platform-pro.iam.gserviceaccount.com` | deploy, rollback |
| `WIF_PROVIDER` | *(same WIF provider — pool is centralized)* | deploy, rollback |
| `CDN_URL_MAP` | `platform-pro-lb-urlmap` | deploy, rollback (only if CDN) |

> Enable **Required reviewers** as protection rule for production deployments.

### Quick Reference: Variable Scope

```
Repository (shared)
├── AR_REPO = europe-west1-docker.pkg.dev/dogfy-host/docker
│
├── Environment: stg
│   ├── GCP_PROJECT  = dogfy-platform-stg
│   ├── SA_EMAIL     = {service}-deployer@dogfy-platform-stg.iam.gserviceaccount.com
│   ├── WIF_PROVIDER = projects/454974353730/.../github-oidc
│   └── CDN_URL_MAP  = platform-stg-lb-urlmap          (optional)
│
└── Environment: production
    ├── GCP_PROJECT  = dogfy-platform-pro
    ├── SA_EMAIL     = {service}-deployer@dogfy-platform-pro.iam.gserviceaccount.com
    ├── WIF_PROVIDER = projects/454974353730/.../github-oidc
    └── CDN_URL_MAP  = platform-pro-lb-urlmap           (optional)
```

### Caller Workflow Permissions

Any caller workflow that uses these reusable workflows **must** declare permissions explicitly. This is a GitHub Actions requirement for `id-token: write` (WIF needs it).

```yaml
permissions:
  contents: read
  id-token: write
  pull-requests: write  # only needed if using preview-cloud-run
```

> If the caller doesn't declare `id-token: write`, WIF auth will fail silently with a 403.

---

## Infrastructure Prerequisites

All workflows use **Workload Identity Federation** (WIF) — no service account keys.

For each service, Terraform creates:
- **Runtime SA** (`{service}-runtime@{project}.iam`) — used by Cloud Run at runtime
- **Deployer SA** (`{service}-deployer@{project}.iam`) — used by GitHub Actions via WIF
- **WIF binding** — links the GitHub repo to the deployer SA

The deployer SA has:
- `roles/run.developer` — deploy to Cloud Run
- `roles/artifactregistry.writer` — push images to AR
- `roles/iam.serviceAccountUser` — act as runtime SA
- `roles/compute.loadBalancerAdmin` — CDN cache invalidation (optional, for SPAs with CDN)

All Cloud Run config (env vars, secrets, scaling, networking) is owned by Terraform. CI/CD only deploys images via `gcloud run deploy --image`.

## Source of Truth

These workflows are also tracked in the infrastructure repo at `docs/workflows/reusable/` for version control alongside the Terraform modules that provision the GCP resources they depend on.
