# Remove In-App Self-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution step 0:** Copy this file verbatim to `docs/superpowers/plans/2026-07-07-remove-self-update.md` (create the dir if missing) so it is version-controlled and every dispatched subagent can read it. That copy is the canonical plan.

**Goal:** Remove Flowfolio's in-app self-update mechanism (updater sidecar + one-click apply flow), slim the image, collapse the single-image compose to one service, and keep only the passive "a new version is available" notify path. Ship as v1.3.0.

**Architecture:** The self-update needs a socket-mounting `updater` sidecar, a compose file on disk, the docker CLI + compose plugin in the image, and an app↔updater `request.json`/`status.json` file channel. All of that goes. What stays is the daily GitHub version check, the cached-release store, the `GET /api/version` + `GET /api/update-status` + `POST /api/update/check` + `PUT /api/update/dismiss` endpoints, the dismissable banner, and the Settings "Check for updates" button. When a newer release exists the app now just tells the user to update their own container (`docker compose pull` / Portainer redeploy).

**Tech Stack:** FastAPI + SQLite (backend), Next.js 16 / React 19 / TanStack Query (frontend), Docker single-image (Dockerfile + compose.yml), s6-overlay.

## Context

The in-app self-update was over-engineered for a single-user self-hosted app. It is fragile under Portainer/Synology, requires the app image to carry the docker CLI + compose plugin and mount the host Docker socket via a second `updater` service, and depends on a compose file being present on disk. For a hobby, single-user deployment, `docker compose pull` / Portainer "Pull and redeploy" is trivial and safer to reason about. We accept losing auto-rollback and the mandatory pre-update DB snapshot; the daily backup plus a manual image re-pin is the safety net. This is a pure simplification/removal (v1.3.0 because it removes a capability).

## Global Constraints

- **Version is tag-only.** `APP_VERSION` is baked from the pushed git tag (`Dockerfile` `ARG APP_VERSION`, `release.yml` passes `APP_VERSION=${{ github.ref_name }}`, `config.py` `app_version` falls back to `"dev"`). v1.3.0 needs NO source version edit; the tag drives everything.
- **KEEP (do not touch the behavior of):** `GET /api/version`; `GET /api/update-status` (but strip the 4 updater-progress fields); `POST /api/update/check`; `PUT /api/update/dismiss`; `backend/app/services/update_check.py`; `backend/app/services/update_store.py`; the `version_check` daily cron in `scheduler.py`; `backend/app/core/deps.py` `forbid_in_demo` (used by auth + keys routers); `scripts/backup.sh`; `scripts/restore_local.sh` (script body); `frontend/components/update/UpdateBanner.tsx`; `frontend/components/update/UpdateBannerProvider.tsx`; the Settings "Check for updates" button; `withV` + `updateActionable` in `lib/update-status.ts`.
- **Prose/comment style:** no em-dashes, no semicolons in prose (project owner preference). Applies to code comments and commit messages too.
- **Gating test suite:** `cd backend && uv run python -m pytest -q` must stay green. Frontend gate: `cd frontend && npm run lint && npm run test:unit && npm run build`.
- **Commit scope per task:** stage only the files that task touches (`git add <explicit paths>`), never `git add -A` (the working tree carries unrelated untracked files: `.claude/`, `.agents/`, `Caddyfile.devproxy`, `compose.devproxy.yml`, and a `.gitignore` edit).
- **No back-compat scaffolding.** Delete cleanly. No redirects, shims, or version guards for the removed endpoint/fields.

## File Map

| File | Action |
|---|---|
| `backend/app/services/update_apply.py` | DELETE |
| `backend/tests/test_update_apply.py` | DELETE |
| `backend/tests/test_update_integration.py` | DELETE |
| `backend/app/routers/update.py` | EDIT (drop apply handler + imports + status merge) |
| `backend/app/schemas/update.py` | EDIT (drop `ApplyResponse` + 4 progress fields) |
| `backend/app/core/config.py` | EDIT (drop `update_channel_dir`, reword `app_version` comment) |
| `frontend/components/update/UpdateOverlay.tsx` | DELETE |
| `frontend/components/update/UpdateConfirmDialog.tsx` | DELETE |
| `frontend/lib/update-status.ts` | EDIT (collapse to `withV` + `updateActionable`) |
| `frontend/lib/__tests__/update-status.test.ts` | EDIT (keep only `updateActionable` block) |
| `frontend/components/settings/SoftwareUpdatesSection.tsx` | EDIT (drop apply/overlay, add container-update guidance) |
| `frontend/components/update/UpdateBanner.tsx` | EDIT (reword stale apply comment only) |
| `compose.yml` | EDIT (single `flowfolio` service, drop updater + socket + `update_channel`) |
| `Dockerfile` | EDIT (drop docker CLI + compose-bin COPYs) |
| `compose.demo.yml` | EDIT (reword stale updater comment) |
| `scripts/updater.sh` | DELETE |
| `scripts/test_updater.sh` | DELETE |
| `scripts/restore_local.sh` | EDIT (reword updater comment, keep script) |

**KEEP untouched:** `update_check.py`, `update_store.py`, `scheduler.py`, `deps.py`, `UpdateBannerProvider.tsx`, `backup.sh`, `compose.multi.yml`, `compose.dev.yml`, `compose.test.yml`, and the backend test files `test_update_status_router.py` / `test_update_check.py` / `test_version_endpoint.py` / `test_scheduler.py` / `test_backup_job.py` (none assert a removed symbol).

---

## Task 0: Setup (drop the moot commit, persist the plan)

Local `main` HEAD is `67bb9d7` (v1.2.6 "graceful no-updater overlay" fix), unpushed, 1 commit ahead of `origin/main` (`905b4d1`). It only touches `UpdateOverlay.tsx` (deleted here), `lib/update-status.ts` (rewritten here), and its test (rewritten here), so it is moot. Reset it away so v1.3.0 history is clean.

**Files:**
- Create: `docs/superpowers/plans/2026-07-07-remove-self-update.md`

- [ ] **Step 1: Confirm the git state**

```bash
cd /Users/Luk/Repositories/flowfolio
git log --oneline -1            # expect 67bb9d7
git rev-parse origin/main       # expect 905b4d1...
git status -sb                  # note the pre-existing untracked/modified files
```

- [ ] **Step 2: Reset HEAD to origin/main and clean the 3 moot files**

```bash
git reset origin/main          # mixed reset: HEAD -> 905b4d1, index cleared, working tree untouched
git checkout origin/main -- \
  frontend/components/update/UpdateOverlay.tsx \
  frontend/lib/update-status.ts \
  frontend/lib/__tests__/update-status.test.ts
```

Expected after: `git log --oneline -1` shows `905b4d1`. `git status` shows ONLY the pre-existing unrelated changes (` M .gitignore`, untracked `.claude/` `.agents/` `Caddyfile.devproxy` `compose.devproxy.yml`) and nothing from `67bb9d7`.

- [ ] **Step 3: Persist this plan**

Copy this plan file verbatim to `docs/superpowers/plans/2026-07-07-remove-self-update.md` (mkdir the path if needed).

- [ ] **Step 4: Commit the plan**

```bash
git add docs/superpowers/plans/2026-07-07-remove-self-update.md
git commit -m "docs(plan): remove-self-update implementation plan"
```

---

## Task 1: Backend removal (apply channel, endpoint, schema/config fields)

**Files:**
- Delete: `backend/app/services/update_apply.py`
- Delete: `backend/tests/test_update_apply.py`
- Delete: `backend/tests/test_update_integration.py`
- Modify: `backend/app/routers/update.py`
- Modify: `backend/app/schemas/update.py`
- Modify: `backend/app/core/config.py`

**Interfaces:**
- Produces: `UpdateStatusResponse` with fields `current_version, latest_version, update_available, release_notes_url, dismissed, last_checked, check_failed, is_dev, backups_configured` (no `update_in_progress / update_state / update_message / update_log_tail`). The `POST /api/update/apply` route and `ApplyResponse` no longer exist.

- [ ] **Step 1: Delete the apply service and its tests**

```bash
git rm backend/app/services/update_apply.py \
       backend/tests/test_update_apply.py \
       backend/tests/test_update_integration.py
```

- [ ] **Step 2: Edit `backend/app/routers/update.py`**

Remove, in this file only:
- the `update_apply` import block (imports of `IN_FLIGHT_STATES`, `read_update_status`, `request_update`),
- `ApplyResponse` from the `schemas.update` import line,
- `forbid_in_demo` from its import (it is now unused HERE; leave `deps.py` and its other importers alone),
- inside `get_update_status`: the `status = read_update_status()` line/comment and the 4 kwargs `update_in_progress=…, update_state=…, update_message=…, update_log_tail=…` passed to `UpdateStatusResponse(...)`,
- the entire `apply_update` handler (the `POST /api/update/apply` route, `response_model=ApplyResponse`, `Depends(forbid_in_demo)`).

Leave `get_version`, `get_update_status` (now without the merge), `check_for_update`, `dismiss_version`, and both router objects intact.

- [ ] **Step 3: Edit `backend/app/schemas/update.py`**

Remove the 4 updater-progress fields from `UpdateStatusResponse` (`update_in_progress`, `update_state`, `update_message`, `update_log_tail`, plus their comment) and delete the entire `ApplyResponse` class. Keep `VersionResponse`, `CheckResponse`, `DismissBody`, and the rest of `UpdateStatusResponse`.

- [ ] **Step 4: Edit `backend/app/core/config.py`**

Delete the `update_channel_dir: str = "/update"` setting and its 4-line explanatory comment. Reword the `app_version` comment (it currently says "Self-update.") to:

```python
    # Reported by GET /api/version and the update-check banner. APP_VERSION is
    # baked at build time via the release workflow's --build-arg; falls back to
    # "dev" for local/untagged builds.
    app_version: str = "dev"
```

- [ ] **Step 5: Run the backend suite**

Run: `cd backend && uv run python -m pytest -q`
Expected: PASS (0 failures). The deleted apply tests are gone; the kept `test_update_status_router.py` / `test_update_check.py` still pass because they never asserted a removed field.

- [ ] **Step 6: Grep the backend for dangling references**

Run:
```bash
grep -rniE 'update_apply|ApplyResponse|update_channel_dir|update_in_progress|update_state|update_message|update_log_tail|read_update_status|request_update|IN_FLIGHT_STATES' backend/app backend/tests
```
Expected: NO output.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/update.py backend/app/schemas/update.py backend/app/core/config.py
git add -u backend/app/services backend/tests
git commit -m "$(cat <<'EOF'
refactor(update): remove the self-update apply channel and endpoint

Drop the app-to-updater request.json/status.json file channel
(update_apply.py), the POST /api/update/apply endpoint and ApplyResponse
schema, the four updater-progress fields on UpdateStatusResponse, and the
update_channel_dir setting. The daily version check, cached-release store,
and the version/status/check/dismiss endpoints stay.

BREAKING CHANGE: in-app self-update is removed. Update by pulling the new
image (docker compose pull / Portainer redeploy).
EOF
)"
```

---

## Task 2: Frontend removal (overlay UI) + Settings rework

**Files:**
- Delete: `frontend/components/update/UpdateOverlay.tsx`
- Delete: `frontend/components/update/UpdateConfirmDialog.tsx`
- Modify: `frontend/lib/update-status.ts`
- Modify: `frontend/lib/__tests__/update-status.test.ts`
- Modify: `frontend/components/settings/SoftwareUpdatesSection.tsx`
- Modify: `frontend/components/update/UpdateBanner.tsx` (comment only)

**Interfaces:**
- Consumes: `GET /api/update-status` now returns no `update_*` progress fields (Task 1).
- Produces: `lib/update-status.ts` exports exactly `withV`, `UpdateActionableInput`, `updateActionable`. `UpdateOverlay` / `UpdateConfirmDialog` no longer exist. `UpdateBannerProvider` + `UpdateBanner` unchanged in behavior.

- [ ] **Step 1: Delete the overlay + confirm-dialog components**

```bash
git rm frontend/components/update/UpdateOverlay.tsx \
       frontend/components/update/UpdateConfirmDialog.tsx
```

- [ ] **Step 2: Collapse `frontend/lib/update-status.ts`**

Remove every overlay-only export: `OverlayPhase`, `DeriveOverlayInput`, `deriveOverlayPhase`, `OverlayCopy`, `overlayCopy`, `MAX_WAIT_MS`, `PREPARING_STALL_MS`, `StuckInput`, `isStuck`, `stuckCopy`, AND `versionsMatch` (now dead: its only non-test caller was `deriveOverlayPhase`). Keep exactly `withV`, `interface UpdateActionableInput`, and `function updateActionable`. The file should end as just those three.

- [ ] **Step 3: Trim `frontend/lib/__tests__/update-status.test.ts`**

Reduce the import to `import { updateActionable } from "../update-status";`. Delete every `describe`/`it` block for `deriveOverlayPhase`, `versionsMatch`, `overlayCopy`, `isStuck`, `stuckCopy`, and the shared `base` fixture they used. Keep only the `updateActionable` block and its `actionableBase` fixture.

- [ ] **Step 4: Rework `frontend/components/settings/SoftwareUpdatesSection.tsx`**

Apply these edits:

(a) Drop the imports `UpdateConfirmDialog` and `UpdateOverlay`.

(b) In the local `interface UpdateStatusResponse`, delete the 4 progress fields (`update_in_progress`, `update_state`, `update_message`, `update_log_tail`). Delete the entire `interface ApplyResponse`.

(c) Delete `const [confirmOpen, setConfirmOpen] = useState(false);` and `const [overlayOpen, setOverlayOpen] = useState(false);`, the whole `applyMutation` block, and the derived `const inProgress = …` / `const busy = …` lines.

(d) In the "Check for updates" `Button`, change `disabled={busy || checkMutation.isPending}` to `disabled={checkMutation.isPending}`.

(e) Delete the "Update now" `Button` block (the `updateAvailable ? <Button onClick={() => setConfirmOpen(true)} …>` element), the `<UpdateConfirmDialog … />` block, and the `<UpdateOverlay … />` block.

(f) Replace the component docstring with:

```tsx
/**
 * Settings -> Software updates panel. Shows the TRUE current-vs-latest
 * status (ignoring banner dismissal), links release notes out, and when a
 * newer release exists tells the user how to update their own container.
 * "Check for updates" forces an immediate GitHub re-check.
 */
```

(g) Replace the demo-hide comment with:

```tsx
  // Hide the Settings "Software updates" panel in demo mode. The hosted demo
  // is not the user's own container, so container-update guidance does not apply.
  if (config?.demo) return null;
```

(h) Add the container-update guidance block. Insert it immediately after the closing `</div>` of the header row (the `flex items-start justify-between` block), i.e. where the removed dialog/overlay blocks were:

```tsx
      {updateAvailable && data?.latest_version ? (
        <div className="space-y-2 rounded-md border bg-muted/40 p-3">
          <p className="text-sm text-muted-foreground">
            Flowfolio {withV(data.latest_version)} is ready. Update your
            container to upgrade. Your data volume and settings are preserved.
          </p>
          <pre className="overflow-x-auto rounded bg-muted px-3 py-2 text-xs">
            <code>docker compose pull &amp;&amp; docker compose up -d</code>
          </pre>
          <p className="text-sm text-muted-foreground">
            On Portainer, open the stack and choose{" "}
            <span className="font-medium text-foreground">Pull and redeploy</span>.
          </p>
        </div>
      ) : null}
```

Leave the `StatusLine` sub-component, `updateActionable`/`withV` usage, the status query, and `checkMutation` intact. (`backups_configured` stays in the interface as a mirror of the API even though nothing reads it now.)

- [ ] **Step 5: Reword the stale comment in `frontend/components/update/UpdateBanner.tsx`**

The banner is KEPT. Only its comment referencing "the 403 on POST /api/update/apply" is now stale. Reword it to describe the banner as a passive, dismissable notice that links to Settings (no apply path). Do not change any behavior.

- [ ] **Step 6: Run the frontend gate**

Run: `cd frontend && npm run lint && npm run test:unit && npm run build`
Expected: lint clean, unit tests pass (only the `updateActionable` block runs from the trimmed file), build succeeds.

- [ ] **Step 7: Grep the frontend for dangling references**

Run:
```bash
grep -rniE 'UpdateOverlay|UpdateConfirmDialog|deriveOverlayPhase|overlayCopy|isStuck|stuckCopy|OverlayPhase|MAX_WAIT_MS|PREPARING_STALL_MS|versionsMatch|update/apply|update_in_progress|update_state|update_message|update_log_tail' frontend --include='*.ts' --include='*.tsx' | grep -v node_modules
```
Expected: NO output (comment-only mentions in demo.spec.ts about the kept provider are fine if any remain, but the apply/overlay symbols must be gone).

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/update-status.ts frontend/lib/__tests__/update-status.test.ts \
        frontend/components/settings/SoftwareUpdatesSection.tsx \
        frontend/components/update/UpdateBanner.tsx
git add -u frontend/components/update
git commit -m "$(cat <<'EOF'
refactor(update): drop the self-update overlay UI, keep the notify banner

Delete UpdateOverlay + UpdateConfirmDialog and the overlay state machine in
lib/update-status.ts (deriveOverlayPhase/overlayCopy/isStuck/stuckCopy/
versionsMatch and the timing constants). The Settings panel now shows the
current-vs-latest status plus how to update the container (docker compose
pull / Portainer redeploy) instead of a one-click Update now button. The
dismissable UpdateBanner + provider are unchanged.
EOF
)"
```

---

## Task 3: Slim the image and collapse compose

**Files:**
- Delete: `scripts/updater.sh`
- Delete: `scripts/test_updater.sh`
- Modify: `compose.yml`
- Modify: `Dockerfile`
- Modify: `compose.demo.yml`
- Modify: `scripts/restore_local.sh` (comment only)

- [ ] **Step 1: Delete the updater scripts**

```bash
git rm scripts/updater.sh scripts/test_updater.sh
```

- [ ] **Step 2: Rewrite `compose.yml` to a single service**

Replace the whole file with (collapses to one `flowfolio` service; drops the `updater` service, the `update_channel` volume + its mount, and the socket mount):

```yaml
# Single-image distribution artifact: the whole stack (FastAPI + Next.js
# + Caddy + in-process backup) in ONE container supervised by s6-overlay.
# Install is one command:
#   docker compose up -d
#
# The former 4-service base now lives in compose.multi.yml (used by the dev/test
# overlays). To upgrade: docker compose pull && docker compose up -d.
services:
  flowfolio:
    build: .
    # Published by the release workflow. `build: .` is kept so a local build
    # still works for development.
    image: ghcr.io/lukasbloom/flowfolio:latest
    restart: unless-stopped
    ports:
      # All three are published by default so a single `docker compose up -d`
      # works in BOTH modes with no edits:
      #   DOMAIN set   -> Caddy serves ACME HTTPS on 443 (80 redirects); 8080 is idle.
      #   DOMAIN unset -> Caddy serves plain HTTP on 8080 (http://localhost:8080);
      #                   80/443 are idle. Open http://localhost:8080.
      - "80:80"
      - "443:443"
      - "8080:8080"
    environment:
      # Bare hostname => Caddy runs ACME auto-HTTPS; unset => http://localhost:8080.
      - DOMAIN=${DOMAIN:-}
      # Defaults to production (Swagger off + Secure cookie + boot guards). Empty
      # passes through and still resolves to production in the app. Set
      # APP_ENV=development only for a plain-HTTP local trial. See .env.example.
      - APP_ENV=${APP_ENV:-}
      - APP_PASSWORD=${APP_PASSWORD:-}            # optional pre-seed
      - SECRET_KEY=${SECRET_KEY:-}                # optional; auto-gen if unset
      - BACKUP_ENCRYPTION_KEY=${BACKUP_ENCRYPTION_KEY:-}
      - BACKUP_DEST=${BACKUP_DEST:-}              # off-host opt-in
      - BACKUP_RETAIN_DAYS=${BACKUP_RETAIN_DAYS:-30}
      - BACKUP_DIR=/backups
      # Env-based rclone for the off-host remote. Use an alnum/underscore
      # remote name (dots/dashes break the env-var mapping), then point
      # BACKUP_DEST at it, e.g. BACKUP_DEST=OFFHOST:my-bucket/flowfolio/:
      # - RCLONE_CONFIG_OFFHOST_TYPE=s3
      # - RCLONE_CONFIG_OFFHOST_PROVIDER=Backblaze
      # - RCLONE_CONFIG_OFFHOST_ACCESS_KEY_ID=...
      # - RCLONE_CONFIG_OFFHOST_SECRET_ACCESS_KEY=...
    volumes:
      # SQLite DB + Caddy ACME certs (XDG_DATA_HOME=/data/caddy in the image).
      - db_data:/data
      # Local backup artifacts, kept separate from the live DB volume.
      - backups:/backups

volumes:
  db_data:
  backups:
```

- [ ] **Step 3: Edit `Dockerfile`**

Delete the docker-CLI block: the comment "Docker CLI + compose plugin: ... the app process never does. ..." and the two lines:

```dockerfile
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker/compose-bin:v2.32.4 /docker-compose /usr/local/lib/docker/cli-plugins/docker-compose
```

Leave the Caddy COPY above it and the Node COPY below it. `COPY scripts/ /app/scripts/` stays (it still ships backup/restore scripts, minus the two deleted updater scripts).

- [ ] **Step 4: Reword the stale updater comment in `compose.demo.yml`**

Delete/reword the comment block that references the inherited `updater` sidecar and "POST /api/update/apply means the app never writes /update/request.json". There is no service or volume to change in that file, only the comment.

- [ ] **Step 5: Reword the updater comment in `scripts/restore_local.sh`**

Keep the script. Remove the sentence describing the self-update flow using it via `docker exec` (the updater no longer exists). Keep the description of it as a manual local-recovery helper.

- [ ] **Step 6: Validate compose parses to one service**

Run: `docker compose -f compose.yml config --services`
Expected: prints exactly `flowfolio` (no `updater`).

Run: `grep -rniE 'updater|update_channel|docker\.sock|compose-bin|docker:28-cli|/project/compose\.yml' compose.yml compose.demo.yml Dockerfile scripts`
Expected: NO output.

- [ ] **Step 7: Build the single image (confirms removing the docker CLI COPY did not break the build)**

Run: `docker build -t flowfolio:v13test .`
Expected: build succeeds. (Deeper optional check: `scripts/smoke_single_image.sh` if present.)

- [ ] **Step 8: Commit**

```bash
git add compose.yml Dockerfile compose.demo.yml scripts/restore_local.sh
git add -u scripts
git commit -m "$(cat <<'EOF'
build(image): drop the updater sidecar, docker CLI, and socket mount

Collapse the single-image compose.yml to one flowfolio service (no updater
service, no update_channel volume, no /var/run/docker.sock mount) and remove
the docker:28-cli + compose-bin COPYs from the Dockerfile that existed only
for the updater. Delete scripts/updater.sh + scripts/test_updater.sh and
reword the stale updater references in compose.demo.yml and restore_local.sh.
EOF
)"
```

---

## Task 4: Full verification (kept paths still work)

**Files:** none (verification only).

- [ ] **Step 1: Backend gating suite**

Run: `cd backend && uv run python -m pytest -q`
Expected: PASS, 0 failures.

- [ ] **Step 2: Frontend gate**

Run: `cd frontend && npm run lint && npm run test:unit && npm run build`
Expected: all pass.

- [ ] **Step 3: Repo-wide dangling-reference sweep**

Run:
```bash
grep -rniE 'updater|update_apply|UpdateOverlay|update_channel|docker\.sock|compose-bin|docker:28-cli|/project/compose\.yml|update_in_progress|request_update|read_update_status|ApplyResponse|versionsMatch' \
  --include='*.py' --include='*.ts' --include='*.tsx' --include='*.yml' --include='*.yaml' --include='*.sh' \
  Dockerfile backend frontend scripts compose*.yml | grep -v node_modules
```
Expected: NO output. Investigate any hit before proceeding (a leftover comment in a kept file is the only acceptable residue, and even those were reworded).

- [ ] **Step 4: Exercise the kept notify path end to end**

Boot the dev stack and confirm the banner/check path still works without the removed fields:

```bash
docker compose -f compose.multi.yml -f compose.dev.yml up -d
sleep 8
curl -s http://localhost:8080/api/update-status | python3 -m json.tool
```
Expected: JSON with `current_version`, `update_available`, `dismissed`, `check_failed`, etc., and NO `update_in_progress` / `update_state` / `update_message` / `update_log_tail` keys. Then in the browser (or via the app) confirm Settings -> Software updates shows the version + "Check for updates" button, and that `POST /api/update/apply` is gone:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8080/api/update/apply
```
Expected: `404` (route removed). Tear down: `docker compose -f compose.multi.yml -f compose.dev.yml down`.

- [ ] **Step 5: No commit** (verification only). If anything failed, fix it under the owning task before Task 5.

---

## Task 5: Release v1.3.0 (GATED on explicit user confirmation)

**Do not run any command in this task until the project owner explicitly confirms the tag push.** Pushing `main` publishes to the public repo, and the `v1.3.0` tag triggers the Release workflow (builds + publishes the ghcr image).

- [ ] **Step 1: Final green check** — re-run Task 4 Steps 1-2 and confirm both suites are green. Summarize the diff (`git log --oneline origin/main..HEAD`) for the owner.

- [ ] **Step 2: Get explicit confirmation** from the owner to push + tag v1.3.0.

- [ ] **Step 3: Push main**

```bash
git push origin main
```

- [ ] **Step 4: Tag and push the release**

```bash
git tag -a v1.3.0 -m "v1.3.0: remove in-app self-update"
git push origin v1.3.0
```

- [ ] **Step 5: Release notes** — ensure the GitHub Release for `v1.3.0` explains the removal and the new update method (`docker compose pull` / Portainer "Pull and redeploy"), since `update_check` surfaces the release-notes URL to users in the banner. (Follow the repo's existing release-notes convention; if `release.yml` auto-generates, edit the body to add the update-method note.)

- [ ] **Step 6: Watch the Release workflow** succeed and confirm the new ghcr image publishes.

---

## Out of scope (separate follow-ups)

- On the owner's Synology NAS: set `BACKUP_ENCRYPTION_KEY` (the daily backup currently has none) and redeploy to the single-container compose. Not part of this change.

## Self-Review notes

- **Spec coverage:** every item in the removal spec maps to a task. Extras found during exploration and folded in: `scripts/test_updater.sh` (Task 3), `backend/tests/test_update_integration.py` (Task 1), the `compose.demo.yml` + `restore_local.sh` + `UpdateBanner.tsx` stale comments (Tasks 2-3), and the fact that no `docs/DEPLOY.md` or README self-update section exists (docs deliverable dropped by owner decision; in-app Settings copy carries the guidance instead).
- **Decisions applied:** reset to `origin/main` first (Task 0); delete `versionsMatch` as dead code (Task 2); no README/docs change.
- **Type consistency:** frontend `UpdateStatusResponse` mirror and backend `UpdateStatusResponse` both lose the same 4 progress fields. `lib/update-status.ts` exports (`withV`, `updateActionable`) match every remaining import site (`SoftwareUpdatesSection.tsx`). `UpdateBanner.tsx` uses its own local `withV`, unaffected.
