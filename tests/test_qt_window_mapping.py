# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the actual GUI setup statements without requiring Qt in CI.

winId() and WA_NativeWindow create native children, rather than merely reading
or labeling a widget. These tests pin those side effects at both call sites;
physical composition and motion are additionally checked on the device.
"""
import ast
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from relaytv_app import qt_shell_app

pytestmark = pytest.mark.native


def _run_setup_statement(kind, namespace):
    tree = ast.parse(inspect.getsource(qt_shell_app.main))
    if kind == 'overlay':
        # Select the smallest try block containing the native-overlay setup,
        # including its production backend guard and exception handling.
        matches = [node for node in ast.walk(tree) if isinstance(node, ast.Try)
                   and 'overlay.setAttribute(Qt.WA_NativeWindow' in ast.unparse(node)]
        assert matches, 'Native overlay setup was not found'
        statement = min(matches, key=lambda node: len(list(ast.walk(node))))
    else:
        matches = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'wid' for t in node.targets)
                   and 'video_widget.winId()' in ast.unparse(node)]
        assert len(matches) == 1, 'Expected exactly one mpv embed window-ID setup'
        statement = matches[0]
    code = compile(ast.Module(body=[statement], type_ignores=[]), '<Qt window setup>', 'exec')
    exec(code, namespace)


@pytest.mark.parametrize('use_libmpv', [True, False])
def test_overlay_native_child_is_only_created_for_subprocess_mpv(use_libmpv):
    overlay = Mock()
    native_attribute = object()
    _run_setup_statement('overlay', {
        'use_libmpv': use_libmpv,
        'overlay': overlay,
        'Qt': SimpleNamespace(WA_NativeWindow=native_attribute),
    })
    if use_libmpv:
        overlay.setAttribute.assert_not_called()
    else:
        overlay.setAttribute.assert_called_once_with(native_attribute, True)


@pytest.mark.parametrize('use_libmpv', [True, False])
def test_video_native_window_id_is_only_created_for_subprocess_mpv(use_libmpv):
    video = Mock()
    video.winId.return_value = 1234
    namespace = {'use_libmpv': use_libmpv, 'video_widget': video}
    _run_setup_statement('video', namespace)
    if use_libmpv:
        video.winId.assert_not_called()
        assert namespace['wid'] == 0
    else:
        video.winId.assert_called_once_with()
        assert namespace['wid'] == 1234
