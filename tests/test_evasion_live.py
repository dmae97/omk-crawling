"""Live verification against real handshakes and real servers (network required).

Deselected by default by the project's ``-m 'not live'`` addopts. Run explicitly:

    pytest tests/test_evasion_live.py -m live -q

These tests exist because the offline TLS model was reconciled against the wire
rather than against documentation: its cipher lists for Firefox and Safari were
wrong until a real handshake showed the difference. Keeping the comparison as a
test means the model cannot quietly drift back to a plausible-looking guess.

Nothing here touches a protected site. The endpoints are public fingerprint- and
header-echo services built for exactly this purpose.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.live

#: Public TLS fingerprint echo service (returns the JA3 it computed for us).
TLS_ECHO = "https://tls.browserleaks.com/json"
#: Public request echo service.
HEADER_ECHO = "https://httpbin.org/headers"

FAMILIES = ("chrome", "firefox", "safari")


def _ciphers_and_extensions(response_json: dict) -> tuple[list[int], set[int]]:
    """Split a browserleaks ``ja3_text`` into its cipher list and extension set."""
    parts = response_json["ja3_text"].split(",")
    ciphers = [int(value) for value in parts[1].split("-") if value]
    extensions = {int(value) for value in parts[2].split("-") if value}
    return ciphers, extensions


def _wire_ja3(impersonate: str | None) -> dict:
    curl_cffi = pytest.importorskip("curl_cffi")
    kwargs = {"timeout": 30}
    if impersonate:
        kwargs["impersonate"] = impersonate
    return curl_cffi.requests.get(TLS_ECHO, **kwargs).json()


@pytest.mark.parametrize("family", FAMILIES)
def test_model_cipher_list_matches_the_wire(family):
    """The model's cipher list is order-sensitive and must equal the real one."""
    from omk_crawl.fingerprint import PROFILES
    from omk_crawl.tls import hello_for

    profile = next(p for p in PROFILES if p.family == family)
    ciphers, _ = _ciphers_and_extensions(_wire_ja3(profile.impersonate))
    model = hello_for(profile).normalized()
    assert list(model.cipher_suites) == ciphers, f"{profile.impersonate} cipher list drifted"


@pytest.mark.parametrize("family", FAMILIES)
def test_model_extension_set_matches_the_wire(family):
    """Compared as a set: Chromium randomises the wire order on every connection."""
    from omk_crawl.fingerprint import PROFILES
    from omk_crawl.tls import hello_for

    profile = next(p for p in PROFILES if p.family == family)
    _, extensions = _ciphers_and_extensions(_wire_ja3(profile.impersonate))
    model = hello_for(profile).normalized()
    assert set(model.extensions) == extensions, f"{profile.impersonate} extension set drifted"


def test_impersonation_targets_produce_distinct_wire_fingerprints():
    """If two families shared a JA3, the per-family profiles would be cosmetic."""
    seen = {family: _wire_ja3(_impersonate_for(family))["ja3_hash"] for family in FAMILIES}
    assert len(set(seen.values())) == len(FAMILIES), seen


def test_unimpersonated_client_is_distinguishable_from_every_browser():
    """The baseline a site would block, for contrast with the impersonated runs."""
    baseline = _wire_ja3(None)["ja3_hash"]
    for family in FAMILIES:
        assert _wire_ja3(_impersonate_for(family))["ja3_hash"] != baseline


def test_planned_headers_arrive_intact():
    """The coherence story is only real if the server sees the headers we planned."""
    from omk_crawl.evasion import plan_for

    curl_cffi = pytest.importorskip("curl_cffi")
    plan = plan_for("https://httpbin.org")
    response = curl_cffi.requests.get(
        HEADER_ECHO,
        headers=plan.headers(),
        impersonate=plan.profile.impersonate,
        timeout=30,
    )
    seen = {key.lower(): value for key, value in response.json()["headers"].items()}

    assert seen["user-agent"] == plan.profile.user_agent
    assert seen["accept-language"] == plan.profile.accept_language

    sends_hints = plan.profile.family in ("chrome", "edge")
    assert ("sec-ch-ua" in seen) is sends_hints
    if sends_hints:
        assert seen["sec-ch-ua"] == plan.profile.sec_ch_ua
        assert seen["sec-ch-ua-platform"] == f'"{plan.profile.sec_ch_ua_platform}"'


def _impersonate_for(family: str) -> str:
    from omk_crawl.fingerprint import PROFILES

    return next(p for p in PROFILES if p.family == family).impersonate
