# RelayTV Docs

Use this directory as a small operator/product doc set for the public release branch.

## Primary Docs

- `INSTALL.md`: installation, first boot, and environment defaults
- `API.md`: HTTP endpoint reference
- `JELLYFIN_OPERATIONS.md`: Jellyfin runtime config, verification, troubleshooting
- `NATIVE_RUNTIME_OPERATIONS.md`: runtime operations, readiness checks, logging, and soak workflow
- `OPERATIONS_TEST_MATRIX.md`: runtime profile decision table (machine-checked) and per-profile validation checklists
- `RELEASE.md`: release inputs, image traceability, and compliance checklist

## Engineering Review Docs

- `ARCHITECTURE_REVIEW.md`: current architecture findings and recommended refactor roadmap
- `ARCHITECTURE_PHASE_1_ROADMAP.md`: living Phase 1 branch roadmap, milestones, and PR log
- `ARCHITECTURE_PHASE_1_ROUTE_INVENTORY.md`: Phase 1 route inventory and alias guardrail
- `ARCHITECTURE_PHASE_2_ROADMAP.md`: living Phase 2 branch roadmap for the runtime config service
- `ARCHITECTURE_PHASE_2_ENV_INVENTORY.md`: machine-checked env variable inventory and settings-bus classification
- `ARCHITECTURE_PHASE_3_ROADMAP.md`: living Phase 3 branch roadmap for the playback transition service
- `ARCHITECTURE_PHASE_3_TRANSITION_INVENTORY.md`: machine-checked playback transition writer inventory and containment contract
- `ARCHITECTURE_PHASE_4_ROADMAP.md`: living Phase 4 branch roadmap for the Jellyfin product service
- `ARCHITECTURE_PHASE_4_JELLYFIN_INVENTORY.md`: machine-checked Jellyfin route-surface inventory and containment contract
- `ARCHITECTURE_PHASE_5_ROADMAP.md`: living Phase 5 branch roadmap for the optional API token
- `ARCHITECTURE_PHASE_6_ROADMAP.md`: living Phase 6 branch roadmap for the operations test matrix

## Module Ownership Snapshot

- `app/relaytv_app/routes/`: FastAPI route modules and compatibility
  aggregation. Domain routers own public endpoint registration; the aggregate
  package still owns shared route helpers and cross-domain glue.
- `app/relaytv_app/static/ui/`: main web UI stylesheet and JavaScript loaded by
  `/ui`.
- `app/relaytv_app/playback_service.py`: playback transition commands
  (play-now, queue, close, advance, resume, natural end, stop) — the writer
  of playback session state outside `state.py`.
- `app/relaytv_app/player.py`: playback process/runtime adapter (mpv
  lifecycle, Qt shell, CEC, track/property control).
- `app/relaytv_app/state.py`: persisted queue, history, session, and settings
  data.
- `app/relaytv_app/resolver.py`: URL validation, provider classification, and
  stream resolution.
- `app/relaytv_app/integrations/jellyfin_service.py`: Jellyfin product
  behavior — command ingress, stream selection and transcode policy, track
  preferences, metadata enrichment, stopped/progress payloads.
- `app/relaytv_app/integrations/jellyfin_receiver.py`: Jellyfin transport,
  auth, status, catalog cache, and progress/stopped calls.
- `scripts/`: install, doctor, host operations, and release support scripts.

Development history, migration notes, archived docs, deep validation notes, and engineering-only guidance should stay out of the public documentation tree unless they are intentionally converted into operator-facing docs.

## Rule

New docs should usually do one of these:

1. extend an existing primary runbook
2. add a narrowly scoped new operator/product doc
3. stay out of the public repo if they are project notes, plans, migration history, or engineering-only reference material
