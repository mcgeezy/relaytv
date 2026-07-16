# RelayTV Docs

Use this directory as a small operator/product doc set for the public release branch.

## Primary Docs

- `INSTALL.md`: installation, first boot, and environment defaults
- `API.md`: HTTP endpoint reference
- `JELLYFIN_OPERATIONS.md`: Jellyfin/Emby runtime config, verification, troubleshooting
- `EMBY_INTEGRATION_ROADMAP.md`: Emby server support roadmap and live-verification checklist
- `POSTLIVE_REPLAY_RELAY_ROADMAP.md`: playing YouTube replays that are still processing (why mpv can't, how the relay does)
- `NATIVE_RUNTIME_OPERATIONS.md`: runtime operations, readiness checks, logging, and soak workflow
- `OPERATIONS_TEST_MATRIX.md`: runtime profile decision table (machine-checked) and per-profile validation checklists
- `RELEASE.md`: release inputs, image traceability, and compliance checklist

## Engineering Review Docs

The 2026-06/07 architecture review and its six-phase refactor roadmap are
complete (PRs #21–#26, merged 2026-07-03/04).

Historical records (findings and per-phase milestone logs; not updated further):

- `ARCHITECTURE_REVIEW.md`: architecture findings, finding outcomes, and the
  completed refactor roadmap (open follow-ups listed at the end)
- `ARCHITECTURE_PHASE_1_ROADMAP.md` … `ARCHITECTURE_PHASE_6_ROADMAP.md`:
  per-phase milestone logs (routes/UI split, runtime config service, playback
  transition service, Jellyfin product service, optional API token, operations
  test matrix)

Active guardrails (machine-checked by the test suite; regenerate via each
inventory test's `--write` mode after intentional changes):

- `ARCHITECTURE_PHASE_1_ROUTE_INVENTORY.md`: route inventory and alias guardrail (`tests/test_route_inventory.py`)
- `ARCHITECTURE_PHASE_2_ENV_INVENTORY.md`: env variable inventory and settings-bus classification (`tests/test_env_inventory.py`)
- `ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md`: playback transition writer inventory and containment contract (`tests/test_transition_inventory.py`)
- `ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md`: Jellyfin route-surface inventory and containment contract (`tests/test_jellyfin_inventory.py`)
- `OPERATIONS_TEST_MATRIX.md`: runtime profile decision table (`tests/test_runtime_matrix.py`; listed under Primary Docs above)

## Module Ownership Snapshot

- `app/relaytv_app/routes/`: FastAPI route modules and compatibility
  aggregation. Domain routers own public endpoint registration; the aggregate
  package still owns shared route helpers and cross-domain glue.
- `app/relaytv_app/static/ui/`: main web UI stylesheet and JavaScript loaded by
  `/ui`.
- `app/relaytv_app/config.py`: runtime config service — typed env parsing,
  settings bus, and the explicit subprocess env-mirroring boundary.
- `app/relaytv_app/playback_service.py`: playback transition commands
  (play-now, queue, close, advance, resume, natural end, stop) — the writer
  of playback session state outside `state.py`.
- `app/relaytv_app/player.py`: playback process/runtime adapter (mpv
  lifecycle, Qt shell, CEC, track/property control).
- `app/relaytv_app/state.py`: persisted queue, history, session, and settings
  data.
- `app/relaytv_app/resolver.py`: URL validation, provider classification, and
  stream resolution.
- `app/relaytv_app/integrations/jellyfin_service.py`: Jellyfin/Emby product
  behavior — command ingress, stream selection and transcode policy, track
  preferences, metadata enrichment, stopped/progress payloads.
- `app/relaytv_app/integrations/jellyfin_receiver.py`: Jellyfin/Emby
  transport, auth, server-type detection, status, catalog cache, and
  progress/stopped calls.
- `scripts/`: install, doctor, host operations, and release support scripts.

Development history, migration notes, archived docs, deep validation notes, and engineering-only guidance should stay out of the public documentation tree unless they are intentionally converted into operator-facing docs.

## Rule

New docs should usually do one of these:

1. extend an existing primary runbook
2. add a narrowly scoped new operator/product doc
3. stay out of the public repo if they are project notes, plans, migration history, or engineering-only reference material
