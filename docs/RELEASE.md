# RelayTV Release Notes and Compliance Checklist

This document records the release inputs and checks for official RelayTV
artifacts.

## Official Container Image

The public image is:

```text
ghcr.io/mcgeezy/relaytv:latest
```

The immutable release image is built by the Release Please workflow after a
GitHub Release is published. Routine `main` branch CI builds publish the
`ghcr.io/mcgeezy/relaytv:main` tag for branch validation and do not overwrite
the release `latest` tag. The Dockerfile pins its base image by digest:

```text
python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664
```

Base digest lookup used for this pin:

```bash
docker buildx imagetools inspect python:3.13-slim
```

## Build Arguments

Official image build arguments:

```text
RELAYTV_INSTALL_QT=1
RELAYTV_INSTALL_X11_OVERLAY=0
RELAYTV_INSTALL_HEADLESS=0
RELAYTV_INSTALL_NODE=1
RELAYTV_INSTALL_IDLE_BROWSER=0
RELAYTV_INSTALL_OPS_TOOLS=0
RELAYTV_IMAGE_SOURCE=https://github.com/mcgeezy/relaytv
RELAYTV_IMAGE_REVISION=<git sha>
RELAYTV_IMAGE_VERSION=<git ref name or release tag>
```

Local builds may override optional runtime bundles with `docker compose build`
or `docker compose up -d --build`. Those local overrides are not the official
release image profile unless documented in a release.

## OCI Labels

Official images should expose these labels:

```text
org.opencontainers.image.title
org.opencontainers.image.description
org.opencontainers.image.source
org.opencontainers.image.revision
org.opencontainers.image.version
org.opencontainers.image.created
org.opencontainers.image.licenses
```

Inspect labels with:

```bash
docker inspect ghcr.io/mcgeezy/relaytv:latest
```

## Automated Release Flow

RelayTV uses Release Please on pushes to `main`.

Normal feature and fix changes should land through pull requests whose titles
use Conventional Commit format, for example:

```text
feat: add Jellyfin pairing status
fix(player): handle missing media duration
docs: clarify release install path
```

After releasable commits land on `main`, Release Please creates or updates a
release pull request. That pull request owns:

- the `CHANGELOG.md` update
- the `pyproject.toml` version bump
- the `.release-please-manifest.json` version update

This repository was bootstrapped from the current `0.1.0` source baseline, so
the first Release Please run starts changelog collection at the configured
`bootstrap-sha` instead of importing the entire pre-automation history.

When the release pull request is merged, Release Please creates the GitHub
Release and source tag, for example `v0.2.0`. The release workflow then builds
and publishes immutable GHCR image tags:

```text
ghcr.io/mcgeezy/relaytv:v0.2.0
ghcr.io/mcgeezy/relaytv:0.2.0
ghcr.io/mcgeezy/relaytv:0.2
ghcr.io/mcgeezy/relaytv:latest
```

The container image also exposes the release build metadata to the running app
through `RELAYTV_IMAGE_VERSION`, `RELAYTV_IMAGE_REVISION`,
`RELAYTV_IMAGE_CREATED`, and `RELAYTV_IMAGE_SOURCE`. The UI About menu reads
`/app/info` to show the current version, link to the changelog, and report
whether a newer GitHub Release is available. Set
`RELAYTV_UPDATE_CHECK_DISABLED=1` to disable the cached GitHub release lookup.

Do not manually edit `CHANGELOG.md` for normal changes and do not manually tag
normal releases. Use a `Release-As: X.Y.Z` commit footer only when intentionally
overriding Release Please's next version.

Repository administrators must allow GitHub Actions to create pull requests for
the Release Please workflow. If CI checks must run on Release Please-generated
pull requests, create a repository secret named `RELEASE_PLEASE_TOKEN` containing
a fine-grained personal access token or GitHub App token with permission to
write contents and pull requests.

## License and Notice Files

The image includes RelayTV license and notice files under:

```text
/usr/share/doc/relaytv/LICENSE
/usr/share/doc/relaytv/COPYING
/usr/share/doc/relaytv/THIRD_PARTY_LICENSES.md
/usr/share/doc/relaytv/ASSETS.md
```

Generate the release-time third-party inventory with:

```bash
./scripts/generate-third-party-licenses.sh
```

## Runtime Mutation Policy

Official release images disable `yt-dlp` auto-update by default:

```text
RELAYTV_YTDLP_AUTO_UPDATE=0
```

Users may opt in to runtime updates, but audited/reproducible build claims only
cover the image as built and published. Runtime auto-update changes playback
resolver behavior after the image is built and is not part of the audited
release state.

## Source Mapping

For immutable releases, prefer this process:

1. Tag the source revision, for example `vX.Y.Z`.
2. Build/publish the image from that tag or record the exact `main` revision.
3. Attach a source tarball and generated third-party inventory to the GitHub
   Release.
4. Ensure the image label `org.opencontainers.image.revision` matches the source
   revision used for the build.

`latest` is convenient for normal installs, but immutable tags are preferred for
audited deployments.
