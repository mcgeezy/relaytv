# Phase 6 Architecture Roadmap

Date started: 2026-07-03

Branch: `codex/architecture-phase-6` (cut from `main` at the Phase 5 squash
merge, PR #25 / `54081db`)

Phase 6 goal: catch runtime regressions before users do. Turn the implicit
runtime/installer policy (review Finding 9) into a machine-checked runtime
profile matrix — host profile, display session, player backend, Qt runtime
mode, decode profile, fallback behavior — and give operators a documented
per-profile validation checklist covering the review's seven profiles: x11,
wayland, headless, native Qt embedded, Qt external mpv, Raspberry Pi, and
amd64 mini PC.

This is the final phase of the architecture roadmap
(`docs/ARCHITECTURE_REVIEW.md`, Finding 9 and the Phase 6 roadmap section).
When it completes, the no-release hold lifts.

## Working Rules

- Keep Phase 6 work on `codex/architecture-phase-6` until the phase is
  complete.
- Merge small focused PRs into this branch instead of directly into `main`.
- Documentation and tests only pin existing behavior: no changes to runtime
  decisions, installer output, entrypoint policy, or capability payloads. If
  a decision looks wrong while pinning it, document it here instead of
  changing it.
- The matrix tests drive the real decision functions with fake env/host
  inputs — they must not spawn processes, touch `/dev`, or depend on the CI
  host's display stack.
- Update this file whenever a milestone starts, completes, changes scope,
  or uncovers follow-up work.
- Only open the final `codex/architecture-phase-6` to `main` PR after all
  Phase 6 validation gates pass.
- No release until the architecture roadmap completes (user decision
  2026-07-03); this phase ships as a `refactor:` PR that does not trigger
  release-please. Completing this phase ends the hold.

## Scope

In scope:

- A machine-checked runtime profile matrix (`tests/test_runtime_matrix.py`)
  that pins, per profile, the decisions made by:
  - `container_entrypoint._host_profile` (explicit override, Raspberry Pi
    device-tree detection, arm/amd64/generic fallback)
  - `container_entrypoint._normalize_runtime_defaults` (headless remote
    default, embed default for wayland/x11, Raspberry Pi GLES mpv args)
  - `player.qt_runtime_mode_configured` / `qt_runtime_mode_effective`
    (auto → external_mpv on wayland sessions, embed otherwise)
  - `video_profile._decode_profile` and `_av1_allowed` (arm_safe, software,
    intel qsv/vaapi, nvidia cuda, generic vaapi/vulkan; AV1 policy)
- A generated decision table synced into `docs/OPERATIONS_TEST_MATRIX.md`
  by the same test module (`--write`), following the Phase 2/3/4 inventory
  doc pattern: the pinned rows and the generated listing must change in the
  same commit.
- Operator-facing per-profile validation checklists in
  `docs/OPERATIONS_TEST_MATRIX.md`: bring-up command, `doctor.sh`,
  `validate-native-qt-telemetry.sh` where applicable, the expected
  `/runtime/capabilities` and `/status` fields for that profile, and soak
  pointers into `NATIVE_RUNTIME_OPERATIONS.md`.
- Docs wiring: `docs/README.md` and `NATIVE_RUNTIME_OPERATIONS.md` link to
  the matrix.

Out of scope:

- Changing any runtime decision, installer behavior, compose output, or
  capability payload shape.
- Rewriting `install.sh` or modeling its generated output in Python
  (Finding 9's installer-side suggestion stays future work).
- CI hardware-in-the-loop testing; per-profile hardware validation remains
  a documented manual checklist.
- New endpoints or API changes.

## Baseline (measured at phase start)

- Runtime policy is spread across `install.sh`, `docker-compose.yml`,
  `container_entrypoint.py` (`_host_profile`, `_normalize_runtime_defaults`),
  `player.py` (backend/Qt-mode selection), and `video_profile.py` (decode
  profile), with no single place stating what a given host/mode combination
  is expected to decide.
- The decision functions are already unit-testable: `_host_profile` and
  `_normalize_runtime_defaults` take an env dict, `_decode_profile` and
  `_av1_allowed` take explicit inputs, and the `player` mode functions read
  env only.
- Existing runtime test coverage (`tests/test_capability_routes.py`) pins
  the capability endpoint shapes but not the per-profile decision policy.
- `docs/INSTALL.md` describes mode defaults in prose; there is no
  per-profile validation checklist for operators, and the review's seven
  target profiles are not enumerated anywhere.
- The live appliance (amd64 NUC, Wayland session, native Qt embed) is
  available to cross-check one matrix row end-to-end.

## Milestones

### M0: Branch, Roadmap, And Discipline Docs

Status: complete

- Branch cut from `main` at `54081db` (Phase 5 squash merge).
- This roadmap; `AGENTS.md` Phase 6 discipline section (Phase 5 marked
  complete); `docs/README.md` link.

### M1: Machine-Checked Runtime Profile Matrix

Status: complete

- `tests/test_runtime_matrix.py`: `RUNTIME_PROFILE_MATRIX` rows for the
  seven review profiles (plus the decision edge cases worth pinning:
  explicit host-profile override, generic fallback, DRM-less software
  decode, AV1 env override), each driving the real decision functions with
  fake env/host inputs and asserting host profile, Qt runtime mode,
  headless-remote default, Raspberry Pi mpv args, decode profile, and AV1
  policy.
- Doc-sync test + `--write` generator producing the decision table in
  `docs/OPERATIONS_TEST_MATRIX.md`.
- Landed: seven matrix rows (amd64 x11/wayland embed, wayland external
  mpv explicit, unmanaged-mode auto→external on a Wayland session,
  headless-remote with classic mpv, Raspberry Pi wayland embed with GLES
  args, generic arm64) plus five edge-case tests (host-profile override,
  generic fallback, explicit backend selection, decode accel branches,
  AV1 env override). 13 tests; matrix rows assert entrypoint defaults
  exactly (added-keys equality, not subset).

### M2: Operator Validation Checklists And Docs Wiring

Status: complete

- Per-profile validation checklists in `docs/OPERATIONS_TEST_MATRIX.md`
  (bring-up, doctor, telemetry validation, expected capability/status
  fields, soak pointer).
- Links from `docs/README.md` and `NATIVE_RUNTIME_OPERATIONS.md`.
- Landed: common steps (matrix tests, `doctor.sh`, a
  `/runtime/capabilities` field cross-check with
  `backend_runtime_mismatch=False` as the hard gate) plus seven
  profile-specific checklists referencing the existing tooling
  (`host-ops.sh up/native-ready/acceptance`,
  `validate-native-qt-telemetry.sh`, soak playbook) and a reporting note.

### M3: Phase 6 Final Validation

Status: planned

- Full gates: `ruff check app tests`, `PYTHONPATH=app pytest -q`, CI-like
  fresh-venv run.
- Live cross-check: the appliance's `/runtime/capabilities` and `/status`
  match its matrix row (amd64 / Wayland / native Qt embed).
- Open the final `codex/architecture-phase-6` to `main` PR — this completes
  the architecture roadmap.

## PR And Milestone Log

| Date | Item | Notes |
| --- | --- | --- |
| 2026-07-03 | Phase 6 started | Branch cut from `main` at `54081db` (Phase 5 squash merge); roadmap committed. |
