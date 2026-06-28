# Agent Instructions

## Release and Changelog Discipline

Use Conventional Commit titles for commits and pull requests:

- `feat: ...` for user-visible features
- `fix: ...` for bug fixes
- `docs: ...` for documentation-only changes
- `deps: ...` for dependency updates
- `chore: ...` for maintenance that should not trigger a release
- `refactor: ...`, `test: ...`, `build: ...`, or `ci: ...` when those scopes fit

For breaking changes, add `!`, for example:

```text
feat!: change install configuration format
```

When preparing a pull request, include release-note-quality context:

- user impact
- operator/deployment impact
- breaking changes, or `None`
- tests run

Do not manually create normal release tags.
Do not manually edit `CHANGELOG.md` for normal feature, fix, docs, or dependency
changes. Release Please owns version bumps, release pull requests, changelog
generation, Git tags, GitHub Releases, and immutable release image tags.
