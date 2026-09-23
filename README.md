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

It can also upgrade the addons whose files changed, with `click-odoo-update`,
so a deployed data file actually reaches the database — a restart alone leaves
views and data records at their old values. This is **off unless the caller asks
for it**, because it needs `click-odoo-contrib` installed in the project's image
(check that image's requirements before enabling it):

| `upgrade_databases` | Effect |
|---|---|
| unset (default) | Deploy and restart only. Nothing is upgraded. |
| `auto` | Every same-version database on the host except those matching `upgrade_exclude_regex` (default `^copy-`, the production-restored copies, which the restore job upgrades instead). |
| `db1 db2` | Exactly those, never filtered — an explicit list is a decision. |

Asking for an upgrade on an image without `click-odoo-update` fails the deploy
with a message naming the cause, after the code is out.

`i18n_overwrite: true` adds `--i18n-overwrite`, so a `.po` change with the same
msgid and a new msgstr replaces the stored translation (without it Odoo only fills
terms that have none yet). It also replaces translations edited in the UI.

### rehearse-staging.yml

Runs the staging host's `/opt/odoo_restore/restore.sh auto` by hand — the nightly
restore, on demand. The copy database is rebuilt from the latest production backup
and upgraded against the code staging runs with the same `click-odoo-update`
command production is promoted with. The script's rehearsal record is checked and
published on the run, and the rehearsed commit is tagged `rehearsed/<host>/<time>`.
Caller: `templates/caller-rehearse.yml`.

### deploy-production.yml

Promotes one aggregated commit to production. A private repo's `production`
environment holds the SSH key but cannot require reviewers below the Enterprise
plan, and an environment without rules releases its secrets to any job in the
repo that names it. So the caller repo limits the environment to the
`aggregate-config` branch, protects that branch so only promoters push it, and
the caller job runs only when both `github.actor` and `github.triggering_actor`
(a rerun keeps the original dispatcher as the former) are listed promoters: the
dispatch is the approval. A **gate** job refuses unless the last
rehearsal record names the commit with a clean result, its backup postdates the
current deploy, it covered exactly the requested databases with the requested
translation flag, and the commit's own `repos.yml` has no uncommented
`refs/pull/N/head` line. The previous deploy's commit is accepted as a rollback
without a fresh rehearsal. The **deploy** job then, on the host: takes
the shared lock, checks the running image is the one rehearsed under, fetches the
commit, runs the pre-update backup, prints the preview (`dry_run` stops here;
`republish` only recreates the tag of a commit already deployed, when its run lost that step),
stops Odoo, resets the checkout, upgrades every database in a one-off container,
starts Odoo, and records the deploy on the host and as an annotated tag
`prod/<host>/<time>`. Caller: `templates/caller-promote.yml`. Design and
rationale: odoo-ansible `plans/production-deploy-promotion.md`.

The snapshot commit keeps its `repos.yml` (credentials stripped from URLs) so the
gate can read what a candidate was built from.

## Project classification

| Type | Projects | Template |
|------|----------|----------|
| Odoo.sh (aggregate only) | hls, kns, nsy, rbkk, thc | `caller-aggregate-only.yml` |
| Self-hosted (aggregate + deploy) | asx, axls, crh, fal, iai, mi7, ocj, pci, qrtl, rmm | `caller-aggregate-deploy.yml` |

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
| `PRODUCTION_SSH_KEY` | Repo `production` environment | SSH private key for the promotion; released only to that environment's jobs |

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
