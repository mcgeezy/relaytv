# SPDX-License-Identifier: GPL-3.0-only
"""Queue identity must be assigned before concurrent readers can see entries."""
import threading

from relaytv_app import playback_service, player, routes, state


def test_enqueue_publishes_identity_before_persistence(monkeypatch):
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda item: None)
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    entered, release = threading.Event(), threading.Event()
    events = []
    monkeypatch.setattr(routes, "_ui_event_push", lambda name, payload: events.append(payload))

    def blocked_persist():
        entered.set()
        assert release.wait(5)

    monkeypatch.setattr(state, "persist_queue", blocked_persist)
    worker = threading.Thread(target=lambda: playback_service.queue_item(
        {"url": "https://example.com/selected", "title": "Selected"}
    ), daemon=True)
    worker.start()
    try:
        assert entered.wait(5)
        with state.QUEUE_LOCK:
            snapshot = list(state.QUEUE)
            before = state.queue_item_id(snapshot[0])
        assert before, "An entry became visible before its id was assigned"
        routes._ui_event_push_queue("add", queue=snapshot)
        assert events[0]["queue"][0]["queue_id"] == before
        assert state.queue_item_id(state.QUEUE[0]) == before
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_event_serialization_never_assigns_ids_to_shared_items(monkeypatch):
    item = {"url": "https://example.com/legacy", "title": "Legacy"}
    original = dict(item)
    events = []
    monkeypatch.setattr(routes, "_ui_event_push", lambda name, payload: events.append(payload))
    routes._ui_event_push_queue("add", queue=[item])
    assert item == original
    assert events and events[0]["queue"][0]["title"] == "Legacy"


def test_inserting_duplicate_ahead_preserves_original_identity():
    item = {"url": "https://example.com/same"}
    queue = state._RevisionedQueue([item])
    original_id = state.queue_item_id(item)
    queue.insert(0, item)
    assert queue[1] is item
    assert state.queue_item_id(queue[1]) == original_id
    assert state.queue_item_id(queue[0]) != original_id
