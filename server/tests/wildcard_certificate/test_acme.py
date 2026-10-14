import json
import secrets

import josepy as jose
import pytest
import requests
from acme import challenges
from acme.jws import JWS
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

from server.wildcard_certificate.acme import AcmeClient, AcmeError
from tests.wildcard_certificate.fake_ca import FakeCa

BASE = "https://acme.test"
ACCOUNT_URL = f"{BASE}/acct/1"


@pytest.fixture(autouse=True)
def no_polling_delay(monkeypatch):
    monkeypatch.setattr("acme.client.time.sleep", lambda seconds: None)


class FakeAcmeServer(BaseAdapter):
    """Enough of RFC 8555 to drive a DNS-01 order, verifying every JWS."""

    def __init__(self, dns, fail_validation=False):
        super().__init__()
        self.ca = FakeCa()
        self.dns = dns
        self.fail_validation = fail_validation
        self.nonces = set()
        self.account_key = None
        self.accounts_created = 0
        self.identifiers = []
        self.authorization_status = {}
        self.tokens = {}
        self.order_status = "pending"
        self.certificate_pem = None
        self.answered = []

    def close(self):
        pass

    def send(self, request, **kwargs):
        if request.url == f"{BASE}/directory":
            return self._reply(request, {
                "newNonce": f"{BASE}/nonce",
                "newAccount": f"{BASE}/account",
                "newOrder": f"{BASE}/order",
            })
        if request.method == "HEAD":
            return self._reply(request)
        payload = self._verify(request)
        return self._route(request, payload)

    def _reply(self, request, body=b"", status=200, headers=None, content_type="application/json"):
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response.headers = CaseInsensitiveDict({
            "Replay-Nonce": self._nonce(),
            "Content-Type": content_type,
            **(headers or {}),
        })
        response._content = json.dumps(body).encode() if isinstance(body, dict) else body
        return response

    def _nonce(self):
        nonce = jose.b64encode(secrets.token_bytes(16)).decode()
        self.nonces.add(nonce)
        return nonce

    def _verify(self, request):
        assert request.headers["Content-Type"] == "application/jose+json"
        jws = JWS.json_loads(request.body)
        header = jws.signature.combined
        assert header.alg == jose.ES256
        assert header.url == request.url
        nonce = jose.b64encode(header.nonce).decode()
        assert nonce in self.nonces, "nonce reused or invented"
        self.nonces.discard(nonce)
        if header.jwk:
            self.account_key = header.jwk
        else:
            assert header.kid == ACCOUNT_URL
        assert jws.verify(self.account_key)
        return json.loads(jws.payload) if jws.payload else None

    def _route(self, request, payload):
        url = request.url
        if url == f"{BASE}/account":
            self.accounts_created += 1
            status = 201 if self.accounts_created == 1 else 200
            return self._reply(request, {"status": "valid"}, status, {"Location": ACCOUNT_URL})
        if url == f"{BASE}/order":
            self.identifiers = [i["value"] for i in payload["identifiers"]]
            for index, _ in enumerate(self.identifiers):
                self.authorization_status[index] = "pending"
                self.tokens[index] = secrets.token_bytes(16)
            return self._reply(request, self._order(), 201, {"Location": f"{BASE}/order/1"})
        if url == f"{BASE}/order/1":
            return self._reply(request, self._order())
        if url.startswith(f"{BASE}/authz/"):
            return self._reply(request, self._authorization(int(url.rsplit("/", 1)[1])))
        if url.startswith(f"{BASE}/chall/"):
            return self._answer(request, int(url.rsplit("/", 1)[1]))
        if url == f"{BASE}/finalize":
            csr = x509.load_der_x509_csr(jose.decode_b64jose(payload["csr"]))
            names = csr.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
            self.certificate_pem = self.ca.issue(csr.public_key(), names)
            self.order_status = "valid"
            return self._reply(request, self._order())
        if url == f"{BASE}/cert/1":
            return self._reply(
                request,
                self.certificate_pem.encode(),
                content_type="application/pem-certificate-chain",
            )
        raise AssertionError(f"unexpected {url}")

    def _answer(self, request, index):
        self.answered.append(index)
        expected = challenges.DNS01(token=self.tokens[index]).validation(self.account_key)
        visible = expected in self.dns.published.get(self._record(index), set())
        valid = visible and not self.fail_validation
        self.authorization_status[index] = "valid" if valid else "invalid"
        if all(status == "valid" for status in self.authorization_status.values()):
            self.order_status = "ready"
        return self._reply(
            request,
            self._challenge(index),
            headers={"Link": f'<{BASE}/authz/{index}>;rel="up"'},
        )

    def _record(self, index):
        return f"_acme-challenge.{self.identifiers[index].removeprefix('*.')}"

    def _order(self):
        order = {
            "status": self.order_status,
            "identifiers": [{"type": "dns", "value": value} for value in self.identifiers],
            "authorizations": [f"{BASE}/authz/{i}" for i in range(len(self.identifiers))],
            "finalize": f"{BASE}/finalize",
        }
        if self.order_status == "valid":
            order["certificate"] = f"{BASE}/cert/1"
        return order

    def _challenge(self, index):
        challenge = {
            "type": "dns-01",
            "url": f"{BASE}/chall/{index}",
            "token": jose.b64encode(self.tokens[index]).decode(),
            "status": self.authorization_status[index],
        }
        if self.authorization_status[index] == "invalid":
            challenge["error"] = {
                "type": "urn:ietf:params:acme:error:unauthorized",
                "detail": "No TXT records found",
            }
        return challenge

    def _authorization(self, index):
        identifier = self.identifiers[index]
        return {
            "status": self.authorization_status[index],
            "identifier": {"type": "dns", "value": identifier.removeprefix("*.")},
            "wildcard": identifier.startswith("*."),
            "challenges": [
                {"type": "http-01", "url": f"{BASE}/chall/http", "token": "x" * 43, "status": "pending"},
                self._challenge(index),
            ],
        }


class FakeDns:
    def __init__(self):
        self.published: dict[str, set[str]] = {}
        self.waited: dict[str, set[str]] = {}
        self.cleaned = []

    def present(self, record_name, value):
        self.published.setdefault(record_name, set()).add(value)
        return (record_name, value)

    def wait_until_visible(self, record_name, values):
        self.waited[record_name] = set(values)

    def cleanup(self, record):
        self.cleaned.append(record)
        self.published[record[0]].discard(record[1])


def make_client(server, account_key=None):
    session = requests.Session()
    session.mount(BASE, server)
    return AcmeClient(
        f"{BASE}/directory",
        account_key or ec.generate_private_key(ec.SECP256R1()),
        session=session,
    )


def san_names(chain):
    leaf = x509.load_pem_x509_certificates(chain.encode())[0]
    return leaf, set(
        leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        .value.get_values_for_type(x509.DNSName)
    )


def test_issues_wildcard_and_apex_certificate_through_dns01():
    dns = FakeDns()
    server = FakeAcmeServer(dns)
    key = ec.generate_private_key(ec.SECP256R1())

    chain = make_client(server).issue(["buzz.example.com", "*.buzz.example.com"], key, dns)

    leaf, names = san_names(chain)
    assert leaf.public_key() == key.public_key()
    assert names == {"buzz.example.com", "*.buzz.example.com"}
    assert list(dns.waited) == ["_acme-challenge.buzz.example.com"]
    assert len(dns.waited["_acme-challenge.buzz.example.com"]) == 2
    assert sorted(server.answered) == [0, 1]
    assert len(dns.cleaned) == 2


def test_existing_account_is_reused_by_its_key():
    dns = FakeDns()
    server = FakeAcmeServer(dns)
    account_key = ec.generate_private_key(ec.SECP256R1())
    make_client(server, account_key).issue(
        ["buzz.example.com"], ec.generate_private_key(ec.SECP256R1()), dns
    )

    chain = make_client(server, account_key).issue(
        ["buzz.example.com"], ec.generate_private_key(ec.SECP256R1()), dns
    )

    assert server.accounts_created == 2
    assert "BEGIN CERTIFICATE" in chain


def test_failed_validation_raises_and_still_removes_records():
    dns = FakeDns()
    server = FakeAcmeServer(dns, fail_validation=True)

    with pytest.raises(AcmeError, match="buzz.example.com is invalid: No TXT records found"):
        make_client(server).issue(
            ["buzz.example.com"], ec.generate_private_key(ec.SECP256R1()), dns
        )

    assert len(dns.cleaned) == 1
    assert dns.published == {"_acme-challenge.buzz.example.com": set()}


def test_unreachable_server_raises_acme_error():
    client = AcmeClient("http://127.0.0.1:9/directory", ec.generate_private_key(ec.SECP256R1()))

    with pytest.raises(AcmeError):
        client.issue(["buzz.example.com"], ec.generate_private_key(ec.SECP256R1()), FakeDns())


def test_rejects_account_keys_other_than_p256():
    with pytest.raises(ValueError):
        AcmeClient(f"{BASE}/directory", ec.generate_private_key(ec.SECP384R1()))
