import threading
from collections.abc import Callable
from pathlib import Path
from sqlite3 import Connection


class ServingContentRoots:
    """The content root currently served for each site."""

    def __init__(self):
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._roots: dict[str, Path] = {}
        self._readers: dict[Path, int] = {}
        self._reader_sites: dict[Path, str] = {}
        self._pending_cleanup: dict[Path, Callable[[Path], object]] = {}

    def load(self, conn: Connection, sites_dir: Path, deployments_dir: Path) -> None:
        rows = conn.execute(
            "SELECT sites.name, active.deployment_number "
            "FROM sites LEFT JOIN active_site_deployments AS active "
            "ON active.site_name = sites.name"
        ).fetchall()
        roots = {
            row["name"]: (
                deployments_dir / row["name"] / str(row["deployment_number"])
                if row["deployment_number"] is not None
                else sites_dir / row["name"]
            )
            for row in rows
        }
        with self._lock:
            self._roots = roots

    def acquire(self, site_name: str) -> tuple[Path | None, Callable[[], None]]:
        with self._lock:
            root = self._roots.get(site_name)
            if root is not None:
                self._readers[root] = self._readers.get(root, 0) + 1
                self._reader_sites[root] = site_name

        def release() -> None:
            if root is None:
                return
            with self._idle:
                readers = self._readers[root] - 1
                if readers:
                    self._readers[root] = readers
                    return
                cleanup = self._pending_cleanup.pop(root, None)
                self._readers.pop(root)
                if cleanup is None:
                    self._reader_sites.pop(root)
                    self._idle.notify_all()
            if cleanup:
                try:
                    cleanup(root)
                finally:
                    with self._idle:
                        self._reader_sites.pop(root)
                        self._idle.notify_all()

        return root, release

    def discard_when_unused(
        self, root: Path, cleanup: Callable[[Path], object]
    ) -> None:
        with self._lock:
            if root in self._roots.values():
                raise RuntimeError("Cannot discard a content root that is still serving")
            if self._readers.get(root):
                self._pending_cleanup[root] = cleanup
                return
        cleanup(root)

    def replace(self, site_name: str, root: Path) -> Path | None:
        with self._lock:
            previous = self._roots.get(site_name)
            self._roots = {**self._roots, site_name: root}
            return previous

    def stop_serving(self, site_name: str) -> Path | None:
        with self._idle:
            previous = self._roots.get(site_name)
            self._roots = {
                name: root for name, root in self._roots.items() if name != site_name
            }
            return previous

    def wait_until_idle(self, site_name: str) -> None:
        with self._idle:
            while site_name in self._reader_sites.values():
                self._idle.wait()
