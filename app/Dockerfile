FROM python:3.13-slim@sha256:d168b8d9eb761f4d3fe305ebd04aeb7e7f2de0297cec5fb2f8f6403244621664

ARG RELAYTV_INSTALL_QT=1
ARG RELAYTV_INSTALL_X11_OVERLAY=0
ARG RELAYTV_INSTALL_HEADLESS=0
ARG RELAYTV_INSTALL_NODE=1
ARG RELAYTV_INSTALL_IDLE_BROWSER=0
ARG RELAYTV_INSTALL_OPS_TOOLS=0
ARG RELAYTV_IMAGE_SOURCE="https://github.com/mcgeezy/relaytv"
ARG RELAYTV_IMAGE_REVISION="local"
ARG RELAYTV_IMAGE_VERSION="local"
ARG RELAYTV_IMAGE_CREATED=""

LABEL org.opencontainers.image.title="RelayTV" \
      org.opencontainers.image.description="Local-first TV playback and automation endpoint" \
      org.opencontainers.image.source="${RELAYTV_IMAGE_SOURCE}" \
      org.opencontainers.image.revision="${RELAYTV_IMAGE_REVISION}" \
      org.opencontainers.image.version="${RELAYTV_IMAGE_VERSION}" \
      org.opencontainers.image.created="${RELAYTV_IMAGE_CREATED}" \
      org.opencontainers.image.licenses="GPL-3.0-only"

ENV RELAYTV_IMAGE_SOURCE="${RELAYTV_IMAGE_SOURCE}" \
    RELAYTV_IMAGE_REVISION="${RELAYTV_IMAGE_REVISION}" \
    RELAYTV_IMAGE_VERSION="${RELAYTV_IMAGE_VERSION}" \
    RELAYTV_IMAGE_CREATED="${RELAYTV_IMAGE_CREATED}"

RUN set -eux; \
    apt-get update; \
    pkg_is_installable() { \
      candidate="$(apt-cache policy "$1" | awk '/Candidate:/ {print $2}')"; \
      [ -n "$candidate" ] && [ "$candidate" != "(none)" ]; \
    }; \
    pkgs="mpv libmpv2 cec-utils ca-certificates curl ffmpeg alsa-utils mesa-va-drivers mesa-vulkan-drivers libva-drm2 libdrm2 libgbm1 libegl1 libvulkan1 libwayland-client0 libwayland-cursor0 libwayland-egl1 libgl1-mesa-dri fontconfig fonts-noto-color-emoji"; \
    # Debian package names differ across releases/architectures. \
    # Prefer Mesa-specific names when truly installable, otherwise use virtual libs. \
    if pkg_is_installable libgl1-mesa-glx; then \
      pkgs="$pkgs libgl1-mesa-glx"; \
    else \
      pkgs="$pkgs libgl1"; \
    fi; \
    if pkg_is_installable libgles2-mesa; then \
      pkgs="$pkgs libgles2-mesa"; \
    else \
      pkgs="$pkgs libgles2"; \
    fi; \
    if [ "${RELAYTV_INSTALL_NODE}" = "1" ]; then \
      pkgs="$pkgs nodejs"; \
    fi; \
    if [ "${RELAYTV_INSTALL_HEADLESS}" = "1" ]; then \
      pkgs="$pkgs xvfb x11vnc"; \
    fi; \
    if [ "${RELAYTV_INSTALL_OPS_TOOLS}" = "1" ]; then \
      pkgs="$pkgs mesa-utils procps socat"; \
    fi; \
    if [ "${RELAYTV_INSTALL_X11_OVERLAY}" = "1" ]; then \
      pkgs="$pkgs python3-gi gir1.2-gtk-3.0"; \
      if pkg_is_installable gir1.2-webkit2-4.1; then \
        pkgs="$pkgs gir1.2-webkit2-4.1"; \
      elif pkg_is_installable gir1.2-webkit2-4.0; then \
        pkgs="$pkgs gir1.2-webkit2-4.0"; \
      fi; \
      if pkg_is_installable libwebkit2gtk-4.1-0; then \
        pkgs="$pkgs libwebkit2gtk-4.1-0"; \
      elif pkg_is_installable libwebkit2gtk-4.0-37; then \
        pkgs="$pkgs libwebkit2gtk-4.0-37"; \
      fi; \
    fi; \
    if [ "${RELAYTV_INSTALL_QT}" = "1" ]; then \
      qt_pkgs="libnss3 libnspr4 libxcomposite1 libxdamage1 libxkbcommon0 libxkbcommon-x11-0 libxkbfile1 libxrandr2 libatk-bridge2.0-0 libasound2 libx11-xcb1 libxcb-cursor0 libxcb-glx0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xfixes0"; \
      if pkg_is_installable qt6-wayland; then \
        qt_pkgs="$qt_pkgs qt6-wayland"; \
      fi; \
      if pkg_is_installable qt6-webengine; then \
        qt_pkgs="$qt_pkgs qt6-webengine"; \
      fi; \
      if pkg_is_installable libqt6webenginecore6; then \
        qt_pkgs="$qt_pkgs libqt6webenginecore6"; \
      fi; \
      if pkg_is_installable libqt6webenginewidgets6; then \
        qt_pkgs="$qt_pkgs libqt6webenginewidgets6"; \
      fi; \
      if pkg_is_installable libqt6openglwidgets6; then \
        qt_pkgs="$qt_pkgs libqt6openglwidgets6"; \
      fi; \
      if [ "${RELAYTV_INSTALL_IDLE_BROWSER}" = "1" ] && pkg_is_installable chromium; then \
        qt_pkgs="$qt_pkgs chromium"; \
      fi; \
      pkgs="$pkgs $qt_pkgs"; \
    fi; \
    apt-get install -y --no-install-recommends $pkgs; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64) libdir="/usr/lib/aarch64-linux-gnu" ;; \
      amd64) libdir="/usr/lib/x86_64-linux-gnu" ;; \
      *) libdir="/usr/lib" ;; \
    esac; \
    # Compatibility path: some tooling expects Mesa drivers under <multiarch>/mesa,
    # while Debian installs DRI drivers under <multiarch>/dri.
    if [ -d "$libdir/dri" ] && [ ! -e "$libdir/mesa" ]; then \
      ln -s "$libdir/dri" "$libdir/mesa"; \
    fi; \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONPATH=/usr/lib/python3/dist-packages

# Lean default image: native Qt runtime only. Optional bundles are enabled via
# build args for X11 overlay fallback, headless/VNC, yt-dlp Node helpers,
# browser-backed idle, and extra ops/debug tools.
RUN set -eux; \
    pip install --no-cache-dir yt-dlp fastapi uvicorn[standard] zeroconf qrcode python-multipart; \
    if [ "${RELAYTV_INSTALL_QT}" = "1" ]; then \
      pip install --no-cache-dir PySide6; \
    fi

WORKDIR /app
COPY app/relaytv_app /app/relaytv_app
COPY LICENSE COPYING THIRD_PARTY_LICENSES.md ASSETS.md /usr/share/doc/relaytv/

# Source files can be checked out with restrictive umask-derived modes on some
# hosts. The container runs as the Compose UID, so normalize read/execute bits
# after COPY instead of depending on source checkout permissions.
RUN chmod -R a+rX /app/relaytv_app /usr/share/doc/relaytv

# If weather/static assets are staged in relaytv_app/static/weather, copy them into
# the PWA path so they are served from /pwa/weather/*.
RUN set -eux; \
    if [ -d /app/relaytv_app/static/weather ]; then \
      mkdir -p /app/relaytv_app/static/pwa/weather; \
      cp -a /app/relaytv_app/static/weather/. /app/relaytv_app/static/pwa/weather/; \
    fi

# Preserve legacy /data/assets references while sourcing canonical assets
# from relaytv_app/static/brand.
RUN set -eux; \
    if [ -d /app/relaytv_app/static/brand ]; then \
      mkdir -p /data/assets; \
      if [ -f /app/relaytv_app/static/brand/splash.png ]; then \
        cp -a /app/relaytv_app/static/brand/splash.png /data/assets/splash.png; \
      fi; \
      if [ -f /app/relaytv_app/static/brand/logo.svg ]; then \
        cp -a /app/relaytv_app/static/brand/logo.svg /data/assets/logo.svg; \
      fi; \
    fi

# Fail image build if any merge-conflict markers slipped into Python sources.
RUN ! grep -R -n -E '^(<<<<<<<|=======|>>>>>>>)' /app/relaytv_app

EXPOSE 8787
ENTRYPOINT ["python", "-m", "relaytv_app.container_entrypoint"]
CMD ["uvicorn", "relaytv_app.main:app", "--host", "0.0.0.0", "--port", "8787"]
