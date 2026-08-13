import threading

from server.serving_content_roots import ServingContentRoots


def test_new_root_can_serve_while_previous_root_cleanup_runs(tmp_path):
    roots = ServingContentRoots()
    root = tmp_path / "old"
    new_root = tmp_path / "new"
    roots.replace("site", root)
    _, release_first = roots.acquire("site")
    roots.replace("site", new_root)
    cleanup_started = threading.Event()
    continue_cleanup = threading.Event()

    def cleanup(_root):
        cleanup_started.set()
        assert continue_cleanup.wait(timeout=2)

    roots.discard_when_unused(root, cleanup)
    release_thread = threading.Thread(target=release_first)
    release_thread.start()
    assert cleanup_started.wait(timeout=2)

    acquired_root, release_second = roots.acquire("site")
    assert acquired_root == new_root
    continue_cleanup.set()
    release_thread.join(timeout=2)
    release_second()

    roots.stop_serving("site")
    roots.wait_until_idle("site")


def test_current_root_cannot_be_scheduled_for_cleanup(tmp_path):
    roots = ServingContentRoots()
    root = tmp_path / "site"
    roots.replace("site", root)

    try:
        roots.discard_when_unused(root, lambda _root: None)
    except RuntimeError as error:
        assert str(error) == "Cannot discard a content root that is still serving"
    else:
        raise AssertionError("expected current root cleanup to be rejected")
