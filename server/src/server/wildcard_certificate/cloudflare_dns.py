"""DNS-01 records through the Cloudflare API, confirmed on the zone's own nameservers."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import dns.exception
import dns.resolver

API_URL = "https://api.cloudflare.com/client/v4"
RECORD_TTL_SECONDS = 60
PROPAGATION_TIMEOUT_SECONDS = 180
PROPAGATION_INTERVAL_SECONDS = 5
MAX_RESPONSE_BYTES = 1024 * 1024


class CloudflareDnsError(Exception):
    pass


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    nameservers: tuple[str, ...]


@dataclass(frozen=True)
class TxtRecord:
    zone_id: str
    record_id: str


def lookup_txt_on_nameserver(nameserver: str, record_name: str) -> set[str]:
    addresses = [answer.to_text() for answer in dns.resolver.resolve(nameserver, "A")]
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = addresses
    resolver.lifetime = 5
    try:
        answer = resolver.resolve(record_name, "TXT", raise_on_no_answer=False)
    except dns.resolver.NXDOMAIN:
        return set()
    return {b"".join(record.strings).decode() for record in answer}


class CloudflareDns:
    def __init__(
        self,
        api_token: str,
        open_url: Callable[..., Any] = urlopen,
        lookup_txt: Callable[[str, str], set[str]] = lookup_txt_on_nameserver,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._api_token = api_token
        self._open_url = open_url
        self._lookup_txt = lookup_txt
        self._sleep = sleep
        self._clock = clock
        self._zones: dict[str, Zone] = {}

    def present(self, record_name: str, value: str) -> TxtRecord:
        zone = self._zone_for(record_name)
        result = self._call(
            "POST",
            f"/zones/{zone.id}/dns_records",
            {"type": "TXT", "name": record_name, "content": value, "ttl": RECORD_TTL_SECONDS},
        )
        return TxtRecord(zone.id, result["id"])

    def wait_until_visible(self, record_name: str, values: set[str]) -> None:
        zone = self._zone_for(record_name)
        deadline = self._clock() + PROPAGATION_TIMEOUT_SECONDS
        while True:
            lagging = [
                nameserver
                for nameserver in zone.nameservers
                if not values <= self._visible_values(nameserver, record_name)
            ]
            if not lagging:
                return
            if self._clock() >= deadline:
                raise CloudflareDnsError(
                    f"{record_name} not visible on {', '.join(lagging)} after "
                    f"{PROPAGATION_TIMEOUT_SECONDS}s"
                )
            self._sleep(PROPAGATION_INTERVAL_SECONDS)

    def cleanup(self, record: TxtRecord) -> None:
        self._call("DELETE", f"/zones/{record.zone_id}/dns_records/{record.record_id}")

    def _visible_values(self, nameserver: str, record_name: str) -> set[str]:
        try:
            return self._lookup_txt(nameserver, record_name)
        except (dns.exception.DNSException, OSError):
            return set()

    def _zone_for(self, record_name: str) -> Zone:
        if record_name not in self._zones:
            self._zones[record_name] = self._find_zone(record_name)
        return self._zones[record_name]

    def _find_zone(self, record_name: str) -> Zone:
        labels = record_name.rstrip(".").split(".")
        for start in range(1, len(labels) - 1):
            candidate = ".".join(labels[start:])
            zones = self._call("GET", f"/zones?{urlencode({'name': candidate})}")
            if zones:
                return Zone(
                    zones[0]["id"],
                    zones[0]["name"],
                    tuple(zones[0].get("name_servers", ())),
                )
        raise CloudflareDnsError(f"No Cloudflare zone visible to this token for {record_name}")

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        request = Request(
            f"{API_URL}{path}",
            data=None if payload is None else json.dumps(payload).encode(),
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._open_url(request, timeout=15) as response:
                body = response.read(MAX_RESPONSE_BYTES)
        except HTTPError as exc:
            raise CloudflareDnsError(
                f"Cloudflare {method} {path.split('?')[0]} failed: {exc.code} {api_errors(exc.read(MAX_RESPONSE_BYTES))}"
            ) from exc
        except URLError as exc:
            raise CloudflareDnsError(f"Cloudflare API unreachable: {exc.reason}") from exc
        document = json.loads(body)
        if not document.get("success"):
            raise CloudflareDnsError(f"Cloudflare {method} failed: {document.get('errors')}")
        return document.get("result")


def api_errors(body: bytes) -> str:
    try:
        return "; ".join(error.get("message", "") for error in json.loads(body).get("errors", []))
    except (ValueError, AttributeError):
        return ""
