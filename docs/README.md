# RelayTV Docs

Use this directory as a small operator/product doc set for the public release branch.

## Primary Docs

- `INSTALL.md`: installation, first boot, and environment defaults
- `API.md`: HTTP endpoint reference
- `JELLYFIN_OPERATIONS.md`: Jellyfin runtime config, verification, troubleshooting
- `NATIVE_RUNTIME_OPERATIONS.md`: runtime operations, readiness checks, logging, and soak workflow
- `RELEASE.md`: release inputs, image traceability, and compliance checklist

## Engineering Review Docs

- `ARCHITECTURE_REVIEW.md`: current architecture findings and recommended refactor roadmap
- `ARCHITECTURE_PHASE_1_ROADMAP.md`: living Phase 1 branch roadmap, milestones, and PR log

Development history, migration notes, archived docs, deep validation notes, and engineering-only guidance should stay out of the public documentation tree unless they are intentionally converted into operator-facing docs.

## Rule

New docs should usually do one of these:

1. extend an existing primary runbook
2. add a narrowly scoped new operator/product doc
3. stay out of the public repo if they are project notes, plans, migration history, or engineering-only reference material
