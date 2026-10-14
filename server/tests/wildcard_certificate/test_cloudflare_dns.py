import io
import json
from email.message import Message
from urllib.error import HTTPError

import pytest

from server.wildcard_certificate.cloudflare_dns import CloudflareDns, CloudflareDnsError, TxtRecord


class Response:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self, limit=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeCloudflare:
    def __init__(self, zones=("example.com",)):
        self.zones = set(zones)
        self.requests = []
        self.records = {}

    def __call__(self, request, timeout):
        method, url = request.get_method(), request.full_url
        self.requests.append((method, url.removeprefix("https://api.cloudflare.com/client/v4")))
        assert request.headers["Authorization"] == "Bearer token"
        if method == "GET" and "/zones?name=" in url:
            name = url.split("name=", 1)[1]
            result = (
                [{"id": f"zone-{name}", "name": name, "name_servers": ["ana.ns.cloudflare.com", "bob.ns.cloudflare.com"]}]
                if name in self.zones
                else []
            )
            return Response({"success": True, "result": result})
        if method == "POST":
            record_id = f"rec-{len(self.records) + 1}"
            self.records[record_id] = json.loads(request.data)
            return Response({"success": True, "result": {"id": record_id}})
        if method == "DELETE":
            record_id = url.rsplit("/", 1)[1]
            if record_id not in self.records:
                headers = Message()
                body = json.dumps({"success": False, "errors": [{"message": "Record not found"}]})
                raise HTTPError(url, 404, "nf", headers, io.BytesIO(body.encode()))
            del self.records[record_id]
            return Response({"success": True, "result": {"id": record_id}})
        raise AssertionError((method, url))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_dns(api, visible=lambda nameserver, name: set(), clock=None):
    clock = clock or Clock()
    return CloudflareDns("token", open_url=api, lookup_txt=visible, sleep=clock.sleep, clock=clock)


def test_present_finds_the_zone_by_walking_up_labels_and_creates_a_txt_record():
    api = FakeCloudflare()
    dns = make_dns(api)

    record = dns.present("_acme-challenge.buzz.example.com", "value-1")

    assert record == TxtRecord("zone-example.com", "rec-1")
    assert api.records["rec-1"] == {
        "type": "TXT",
        "name": "_acme-challenge.buzz.example.com",
        "content": "value-1",
        "ttl": 60,
    }
    assert [r for r in api.requests if r[0] == "GET"] == [
        ("GET", "/zones?name=buzz.example.com"),
        ("GET", "/zones?name=example.com"),
    ]


def test_two_values_for_the_same_name_become_two_records_and_zone_is_cached():
    api = FakeCloudflare()
    dns = make_dns(api)

    first = dns.present("_acme-challenge.buzz.example.com", "apex")
    second = dns.present("_acme-challenge.buzz.example.com", "wildcard")
    dns.cleanup(first)

    assert first != second
    assert list(api.records) == ["rec-2"]
    assert sum(1 for method, _ in api.requests if method == "GET") == 2


def test_missing_zone_is_an_error():
    with pytest.raises(CloudflareDnsError, match="No Cloudflare zone"):
        make_dns(FakeCloudflare(zones=())).present("_acme-challenge.buzz.example.com", "v")


def test_api_errors_carry_cloudflare_messages():
    dns = make_dns(FakeCloudflare())

    with pytest.raises(CloudflareDnsError, match="Record not found"):
        dns.cleanup(TxtRecord("zone-example.com", "rec-404"))


def test_waits_until_every_authoritative_nameserver_serves_all_values():
    seen = {"ana.ns.cloudflare.com": 0, "bob.ns.cloudflare.com": 0}

    def visible(nameserver, name):
        seen[nameserver] += 1
        if nameserver == "bob.ns.cloudflare.com" and seen[nameserver] < 3:
            return {"apex"}
        return {"apex", "wildcard"}

    clock = Clock()
    dns = make_dns(FakeCloudflare(), visible, clock)

    dns.wait_until_visible("_acme-challenge.buzz.example.com", {"apex", "wildcard"})

    assert seen["bob.ns.cloudflare.com"] == 3
    assert clock.now == 10


def test_propagation_timeout_names_the_lagging_nameservers():
    dns = make_dns(FakeCloudflare(), lambda nameserver, name: set())

    with pytest.raises(CloudflareDnsError, match="ana.ns.cloudflare.com, bob.ns.cloudflare.com"):
        dns.wait_until_visible("_acme-challenge.buzz.example.com", {"v"})
