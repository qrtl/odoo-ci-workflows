# odoo-ci-workflows

Central reusable GitHub Actions workflows for Odoo project automation.

## Workflows

### gitaggregate.yml

Builds a materialized `_git_aggregated` branch from a `repos.yml` configuration
using [git-aggregator](https://github.com/acsone/git-aggregator). All addon
repos (oca, custom, private) are merged into a single branch with embedded
`.git` directories removed.

### deploy-staging.yml

Deploys the `_git_aggregated` branch to a staging server via SSH. Pulls the
latest branch and restarts the Odoo Docker container.

## Project classification

| Type | Projects | Template |
|------|----------|----------|
| Odoo.sh (aggregate only) | hls, kns, nsy, rbkk, thc | `caller-aggregate-only.yml` |
| Self-hosted (aggregate + deploy) | axls, crh, fal, iai, mi7, pci, qrtl, rmm | `caller-aggregate-deploy.yml` |

## Setup: New project

1. Create an `aggregate-config` branch in the project's `*-private` repo:

   ```bash
   git checkout --orphan aggregate-config
   git rm -rf .
   ```

2. Copy the appropriate caller template from `templates/` to
   `.github/workflows/aggregate.yml`.

   For self-hosted projects, replace `<STAGING_IP>` and `<PROJECT_DIR>` with
   actual values from the Ansible inventory.

3. Create `repos.yml` with the project's aggregation config:

   ```yaml
   ./addons/oca:
     remotes:
       origin: https://github.com/qrtl/<project>-oca.git
     merges:
       - origin <version>

   ./addons/custom:
     remotes:
       origin: https://github.com/qrtl/<project>-custom.git
     merges:
       - origin <version>

   ./addons/private:
     remotes:
       origin: https://<PAT>@github.com/qrtl/<project>-private.git
     merges:
       - origin <version>
   ```

4. Commit and push:

   ```bash
   git add .github/workflows/aggregate.yml repos.yml
   git commit -m "[ADD] aggregate-config: auto-aggregate via central workflows"
   git push origin aggregate-config
   ```

5. Check the Actions tab to verify the workflow runs.

## Setup: Migrate existing project

For projects already using aggregate-config (rbkk-private, hls-repos):

1. On the `aggregate-config` branch, replace `.github/workflows/aggregate.yml`
   with the appropriate caller template.
2. Delete `scripts/aggregate.py` (no longer needed locally).
3. Commit and push. The existing `repos.yml` works unchanged.

## Secrets

| Secret | Level | Purpose |
|--------|-------|---------|
| `GITHUB_TOKEN` | Auto-provided | Pushing to `_git_aggregated` within the same repo |
| `STAGING_SSH_KEY` | Org-level | SSH private key for deploy to staging servers |

### SSH key setup

The `odoo_automation` SSH key is used for staging deploys. On staging servers,
the `from=` IP restriction must be removed from `authorized_keys` to allow
GitHub Actions runners (which use dynamic IPs) to connect. Production servers
keep the `from=` restriction.

Staging `authorized_keys` entry:

```
no-agent-forwarding,no-port-forwarding,no-pty ssh-ed25519 AAAA... quartile
```

Store the private key as org-level GitHub secret `STAGING_SSH_KEY`.

## Architecture

```
repos.yml updated on aggregate-config branch
  -> GitHub Actions: gitaggregate.yml builds _git_aggregated
  -> (Odoo.sh projects) Odoo.sh picks up the branch and rebuilds
  -> (Self-hosted) deploy-staging.yml SSHes into staging, pulls branch, restarts Odoo
```
