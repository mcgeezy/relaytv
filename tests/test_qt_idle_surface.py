# SPDX-License-Identifier: GPL-3.0-only
from unittest.mock import Mock

import pytest

from relaytv_app.qt_shell_app import _sync_libmpv_video_surface


class VideoSurface:
    def __init__(self):
        self.visible = True
        self.transitions = []

    def isVisible(self):
        return self.visible

    def setVisible(self, visible):
        self.visible = visible
        self.transitions.append(visible)


def test_idle_surface_lifecycle_keeps_gl_initialization_and_restores_playback():
    player = Mock()
    player.runtime_snapshot.return_value = {}
    player.render_context_ready.return_value = False
    video = VideoSurface()
    overlay = Mock()

    # Hiding before initializeGL would deadlock a cold start waiting for its
    # render context. The first idle heartbeat must leave the widget mapped.
    _sync_libmpv_video_surface(player, video, overlay)
    assert video.visible
    assert video.transitions == []

    player.render_context_ready.return_value = True
    _sync_libmpv_video_surface(player, video, overlay)
    assert not video.visible

    # A recovered renderer cannot be covered by an idle native GL child.
    recovered_overlay = Mock()
    _sync_libmpv_video_surface(player, video, recovered_overlay)
    assert not video.visible
    assert video.transitions == [False]

    # loadfile can report a path before frames or a duration are available.
    player.runtime_snapshot.return_value = {'mpv_runtime_path': 'test.mp4'}
    _sync_libmpv_video_surface(player, video, recovered_overlay)
    assert video.visible
    recovered_overlay.raise_.assert_called_once_with()

    player.runtime_snapshot.return_value = {
        'mpv_runtime_path': 'test.mp4',
        'mpv_runtime_eof_reached': True,
        'mpv_runtime_playback_active': False,
    }
    _sync_libmpv_video_surface(player, video, recovered_overlay)
    assert not video.visible
    assert video.transitions == [False, True, False]


@pytest.mark.parametrize('snapshot', [
    {'mpv_runtime_path': 'test.mp4', 'mpv_runtime_paused': True,
     'mpv_runtime_playback_active': False},
    {'mpv_runtime_path': 'test.mp4', 'mpv_runtime_core_idle': True,
     'mpv_runtime_playback_active': False},
    {'mpv_runtime_path': 'test.mp4', 'mpv_runtime_playback_active': True},
    {'mpv_runtime_playback_active': True},
])
def test_loaded_paused_buffering_or_active_video_remains_visible(snapshot):
    player = Mock()
    player.runtime_snapshot.return_value = snapshot
    player.render_context_ready.return_value = True
    video = VideoSurface()
    video.visible = False
    _sync_libmpv_video_surface(player, video, Mock())
    assert video.visible
