import json
import math
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from server.traefik_file_provider import DynamicConfigError, TraefikDynamicFile, serialize

ROUTER = {"rule": "Host(`a.example`)", "service": "buzz@docker"}
ROUTERS = {"http": {"routers": {"buzz-a": ROUTER}}}


def test_write_publishes_json_yaml_readable_only_by_owner(tmp_path):
    dynamic = TraefikDynamicFile(tmp_path, "buzz-routes")

    assert dynamic.write(ROUTERS) is True

    assert json.loads((tmp_path / "buzz-routes.yaml").read_text()) == ROUTERS
    assert stat.S_IMODE((tmp_path / "buzz-routes.yaml").stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == ["buzz-routes.yaml"]


def test_serialized_document_is_yaml_that_round_trips():
    assert yaml.safe_load(serialize(ROUTERS)) == ROUTERS


def test_file_is_replaced_atomically_and_left_alone_when_unchanged(tmp_path):
    dynamic = TraefikDynamicFile(tmp_path, "buzz-routes")
    dynamic.write(ROUTERS)
    first_inode = dynamic.path.stat().st_ino

    assert dynamic.write(ROUTERS) is False
    assert dynamic.path.stat().st_ino == first_inode
    assert dynamic.write({"http": {"routers": {"buzz-b": ROUTER}}}) is True
    assert dynamic.path.stat().st_ino != first_inode


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"providers": {"file": {}}},
        {"http": {"routers": {"x": {"priority": math.nan}}}},
        {"http": {"routers": {"x": {"entryPoints": {"https"}}}}},
        {"http": {}},
        {"http": {"routers": {}}},
        {"http": {"routers": {"buzz-a": ROUTER}, "middlewares": {}}},
        {"http": {"routers": {"buzz-a": {**ROUTER, "rule": "Host(`{{ env }}`)"}}}},
        {"http": {"routers": {"buzz-a": {**ROUTER, "rule": "Host(`a`)}}"}}}},
    ],
)
def test_invalid_document_leaves_published_file_untouched(tmp_path, document):
    dynamic = TraefikDynamicFile(tmp_path, "buzz-routes")
    dynamic.write(ROUTERS)

    with pytest.raises(DynamicConfigError):
        dynamic.write(document)

    assert json.loads(dynamic.path.read_text()) == ROUTERS
    assert sorted(p.name for p in tmp_path.iterdir()) == ["buzz-routes.yaml"]


def test_write_failure_is_reported_without_leftovers(tmp_path):
    dynamic = TraefikDynamicFile(tmp_path / "missing", "buzz-routes")

    with pytest.raises(DynamicConfigError, match="Cannot write"):
        dynamic.write(ROUTERS)


@pytest.mark.parametrize("name", ["routes", "buzz.yaml", "buzz_routes", "../buzz", "Buzz-routes", ""])
def test_names_are_limited_to_buzz_prefixed_slugs(tmp_path, name):
    with pytest.raises(ValueError):
        TraefikDynamicFile(tmp_path, name)


def test_concurrent_writers_never_publish_a_partial_file(tmp_path):
    documents = [
        {"http": {"routers": {f"buzz-{writer}": {**ROUTER, "rule": "x" * (writer * 3000 + 1)}}}}
        for writer in range(1, 4)
    ]

    def write_many(document):
        dynamic = TraefikDynamicFile(tmp_path, "buzz-routes")
        for _ in range(50):
            dynamic.write(document)
            assert json.loads(dynamic.path.read_text()) in documents

    with ThreadPoolExecutor(len(documents)) as pool:
        list(pool.map(write_many, documents))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["buzz-routes.yaml"]
