"""Offline tests for the plain-HTTP MITM canary in the proxy pool."""

from __future__ import annotations

from typing import Any

import pytest

from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.rotate_crawl import RotationCrawler
from omk_crawl.tools import proxy_engine_v2 as engine_mod
from omk_crawl.tools.proxy_engine_v2 import ProxyEngineV2, ProxyNode


class TestProxyNodeIntegrityField:
    def test_default_untested(self):
        assert ProxyNode(url="1.2.3.4:80").integrity is None

    def test_cache_roundtrip(self):
        node = ProxyNode(url="1.2.3.4:80", integrity=True)
        assert ProxyNode.from_dict(node.to_dict()).integrity is True
        assert ProxyNode.from_dict({"url": "9.9.9.9:80"}).integrity is None


class _FakeEngine:
    def __init__(self) -> None:
        self.nodes = {
            "untested": ProxyNode(url="1.1.1.1:80", integrity=None),
            "clean": ProxyNode(url="2.2.2.2:80", integrity=True),
            "tampered": ProxyNode(url="3.3.3.3:80", integrity=False),
        }

    def mark_success(self, node, latency_ms=0):
        pass

    def mark_rate_limited(self, node):
        pass

    def mark_dead(self, node):
        pass

    @property
    def _engine(self):
        return self


class TestEngineNextIntegrityFilter:
    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        eng = ProxyEngineV2(session_seed="test")
        fake = _FakeEngine()
        eng._pool = list(fake.nodes.values())
        return eng, fake

    def test_integrity_required_picks_only_clean(self, engine):
        eng, fake = engine
        for _ in range(20):
            node = eng.next(require_integrity=True)
            assert node is fake.nodes["clean"]

    def test_no_requirement_serves_any(self, engine):
        eng, fake = engine
        seen = {eng.next().url for _ in range(60)}
        assert seen == {"1.1.1.1:80", "2.2.2.2:80", "3.3.3.3:80"}

    def test_all_tainted_returns_none(self, engine):
        eng, fake = engine
        eng._pool = [fake.nodes["untested"], fake.nodes["tampered"]]
        assert eng.next(require_integrity=True) is None
        assert eng.next() is not None  # https target unaffected


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body


class _FakeSession:
    def __init__(self, resp=None, raise_exc=False, by_url: dict | None = None):
        self.resp = resp
        self.raise_exc = raise_exc
        self.by_url = by_url or {}

    def get(self, url, **kwargs):
        if self.raise_exc:
            raise OSError("connect refused")
        for frag, resp in self.by_url.items():
            if frag in url:
                return _Ctx(resp)
        return _Ctx(self.resp)

class TestIntegrityCheck:
    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        return ProxyEngineV2(session_seed="test")

    @pytest.mark.asyncio
    async def test_hijack_burns_node(self, engine):
        node = ProxyNode(url="1.2.3.4:80")
        session = _FakeSession(_FakeResp(200, "<html>CyberPanel login</html>"))
        assert await engine._integrity_check(session, node)
        assert node.integrity is False
        assert engine._hash(node.url) in engine._burned

    @pytest.mark.asyncio
    async def test_clean_canary_marks_true(self, engine, monkeypatch):
        monkeypatch.setattr(engine_mod.random, "choice", lambda targets: targets[0])
        node = ProxyNode(url="1.2.3.4:80")
        session = _FakeSession(_FakeResp(200, "203.55.81.2\n"))
        assert await engine._integrity_check(session, node)
        assert node.integrity is True

    @pytest.mark.asyncio
    async def test_transport_failure_leaves_unknown(self, engine):
        node = ProxyNode(url="1.2.3.4:80")
        session = _FakeSession(raise_exc=True)
        assert await engine._integrity_check(session, node)
        assert node.integrity is None

    @pytest.mark.asyncio
    async def test_non_200_leaves_unknown(self, engine):
        node = ProxyNode(url="1.2.3.4:80")
        session = _FakeSession(_FakeResp(407, "auth required"))
        assert await engine._integrity_check(session, node)
        assert node.integrity is None


class _RecordingEngine:
    def __init__(self, nodes):
        self.nodes = nodes
        self.calls: list[dict[str, Any]] = []

    def next(self, exclude=None, require_integrity=False, min_anonymity=None):
        self.calls.append(
            {"require_integrity": require_integrity, "min_anonymity": min_anonymity}
        )
        return self.nodes.pop(0) if self.nodes else None

    def mark_success(self, node, latency_ms=0):
        pass

    def mark_rate_limited(self, node):
        pass

    def mark_dead(self, node):
        pass


class _OkTool:
    def fetch(self, url, **kwargs):
        return CrawlResult(url=url, status=CrawlStatus.OK, html="ok", tool="fake")


class TestRotationIntegrityGate:
    def test_http_target_requests_clean_nodes(self):
        engine = _RecordingEngine([ProxyNode(url="2.2.2.2:80", integrity=True)])
        crawler = RotationCrawler(engine=engine, tool=_OkTool(), auto_refill=False)
        result = crawler.crawl("http://example.com/")
        assert result.ok
        assert engine.calls[0]["require_integrity"] is True

    def test_https_target_uses_full_pool(self):
        engine = _RecordingEngine([ProxyNode(url="1.1.1.1:80", integrity=None)])
        crawler = RotationCrawler(engine=engine, tool=_OkTool(), auto_refill=False)
        result = crawler.crawl("https://example.com/")
        assert result.ok
        assert engine.calls[0]["require_integrity"] is False

    def test_http_untested_node_not_served(self):
        engine = _RecordingEngine([ProxyNode(url="1.1.1.1:80", integrity=None)])
        crawler = RotationCrawler(engine=engine, tool=_OkTool(), auto_refill=False)
        result = crawler.crawl("http://example.com/", attempts_per_url=1)
        # Fake engine serves anything it has — assert the request asked for clean
        assert engine.calls[0]["require_integrity"] is True
        assert result.metadata["rotation"]["attempts"] == 1


class TestCanaryRotation:
    def test_targets_have_endpoint_and_validator(self):
        from omk_crawl.tools.proxy_engine_v2 import CANARY_TARGETS

        assert len(CANARY_TARGETS) >= 3
        for url, pattern in CANARY_TARGETS:
            assert url.startswith("http://")  # canary must be plaintext to detect MITM
            assert callable(pattern.match)

    def test_each_validator_accepts_its_clean_payload(self):
        from omk_crawl.tools.proxy_engine_v2 import CANARY_TARGETS

        clean = {
            "icanhazip.com": "203.0.113.7\n",
            "ifconfig.me": "203.0.113.7",
            "checkip.amazonaws.com": "203.0.113.7\n",
            "ident.me": "203.0.113.7",
            "httpbin.org": '{\n  "origin": "203.0.113.7"\n}\n',
        }
        for url, pattern in CANARY_TARGETS:
            host = url.split("/")[2]
            body = clean[host]
            assert pattern.match(body), f"{host} clean payload rejected"
            assert not pattern.match("<html>injected</html>"), f"{host} accepts injection"

    @pytest.mark.asyncio
    async def test_check_picks_from_target_set(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        eng = ProxyEngineV2(session_seed="test")
        seen: list[str] = []

        class Spy(_FakeSession):
            def get(self, url, **kwargs):
                seen.append(url)
                return _Ctx(_FakeResp(200, "203.0.113.7\n"))

        await eng._integrity_check(Spy(), ProxyNode(url="1.2.3.4:80"))
        allowed = {u for u, _ in engine_mod.CANARY_TARGETS}
        assert seen and seen[0] in allowed

    @pytest.mark.asyncio
    async def test_httpbin_shaped_body_marks_true(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        eng = ProxyEngineV2(session_seed="test")

        def pick(targets):
            return [t for t in targets if "httpbin" in t[0]][0]

        monkeypatch.setattr(engine_mod.random, "choice", pick)
        node = ProxyNode(url="1.2.3.4:80")
        session = _FakeSession(_FakeResp(200, '{\n  "origin": "203.0.113.7"\n}\n'))
        assert await eng._integrity_check(session, node)
        assert node.integrity is True


_CANARY = _FakeResp(200, "203.0.113.7\n")


def _headers_body(**headers):
    import json

    return json.dumps({"headers": headers})


class TestAnonymityProbe:
    """Measured anonymity classification after a canary response."""

    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        # Deterministic canary: the plain-IP target icanhazip (also own-IP probe)
        monkeypatch.setattr(engine_mod.random, "choice", lambda targets: targets[0])
        eng = ProxyEngineV2(session_seed="test")
        eng._own_ip = "203.0.113.7"  # skip the direct self-probe
        return eng

    @pytest.mark.asyncio
    async def test_xff_with_own_ip_is_transparent(self, engine):
        hdrs = _FakeResp(200, _headers_body(**{"X-Forwarded-For": "203.0.113.7"}))
        session = _FakeSession(resp=_CANARY, by_url={"headers": hdrs})
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "transparent"

    @pytest.mark.asyncio
    async def test_forwarded_for_with_own_ip_is_transparent(self, engine):
        hdrs = _FakeResp(200, _headers_body(Forwarded='for="203.0.113.7";proto=http'))
        session = _FakeSession(resp=_CANARY, by_url={"headers": hdrs})
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "transparent"

    @pytest.mark.asyncio
    async def test_via_only_is_anonymous(self, engine):
        hdrs = _FakeResp(200, _headers_body(Via="1.1 proxy.internal"))
        session = _FakeSession(resp=_CANARY, by_url={"headers": hdrs})
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "anonymous"

    @pytest.mark.asyncio
    async def test_xff_with_other_ip_is_anonymous(self, engine):
        hdrs = _FakeResp(200, _headers_body(**{"X-Forwarded-For": "10.0.0.99"}))
        session = _FakeSession(resp=_CANARY, by_url={"headers": hdrs})
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "anonymous"

    @pytest.mark.asyncio
    async def test_no_forwarding_headers_is_elite(self, engine):
        hdrs = _FakeResp(200, _headers_body(Host="httpbin.org", **{"User-Agent": "aiohttp"}))
        session = _FakeSession(resp=_CANARY, by_url={"headers": hdrs})
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "elite"

    @pytest.mark.asyncio
    async def test_probe_failure_stays_unknown(self, engine):
        session = _FakeSession(raise_exc=True)
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "unknown"

    @pytest.mark.asyncio
    async def test_unknown_own_ip_never_claims_elite(self, engine, monkeypatch):
        engine._own_ip = None  # force the direct self-probe, which then fails
        hdrs = _FakeResp(200, _headers_body(**{"X-Forwarded-For": "1.1.1.1"}))
        session = _FakeSession(
            resp=_CANARY,
            by_url={"headers": hdrs, "icanhazip": _FakeResp(200, "garbage")},
        )
        node = ProxyNode(url="1.2.3.4:80")
        await engine._integrity_check(session, node)
        assert node.anonymity == "anonymous"  # headers present, leak unproven


class TestAnonymityGate:
    @pytest.fixture
    def engine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(engine_mod, "POOL_CACHE", tmp_path / "pool.json")
        monkeypatch.setattr(engine_mod, "BURNED_CACHE", tmp_path / "burned.json")
        eng = ProxyEngineV2(session_seed="test")
        eng._pool = [
            ProxyNode(url="t:1", anonymity="transparent", anonymity_probed=True),
            ProxyNode(url="a:1", anonymity="anonymous", anonymity_probed=True),
            ProxyNode(url="e:1", anonymity="elite", anonymity_probed=True),
            ProxyNode(url="u:1", anonymity="unknown"),
        ]
        return eng

    def test_default_none_passes_all(self, engine):
        for _ in range(20):
            assert engine.next() is not None

    def test_anonymous_drops_transparent_and_unknown(self, engine):
        for _ in range(20):
            node = engine.next(min_anonymity="anonymous")
            assert node.anonymity in ("anonymous", "elite")

    def test_elite_drops_everything_but_elite(self, engine):
        for _ in range(20):
            node = engine.next(min_anonymity="elite")
            assert node.anonymity == "elite"

    def test_exhausted_gate_returns_none(self, engine):
        engine._pool = [ProxyNode(url="t:1", anonymity="transparent", anonymity_probed=True)]
        assert engine.next(min_anonymity="anonymous") is None

    def test_persists_through_cache_roundtrip(self):
        node = ProxyNode(url="1.2.3.4:80", anonymity="transparent", anonymity_probed=True)
        assert ProxyNode.from_dict(node.to_dict()).anonymity == "transparent"

    def test_claimed_grade_without_probe_fails_closed(self):
        """Pre-feature/forged caches can claim 'elite' — restart must not trust it."""
        node = ProxyNode.from_dict({"url": "1.2.3.4:80", "anonymity": "elite"})
        assert node.anonymity == "unknown"
        engine_mod_ok = engine_mod._anonymity_ok(node, "anonymous")
        assert engine_mod_ok is False


class TestRotationCrawlerAnonymity:
    def test_default_drops_transparent(self, monkeypatch):
        monkeypatch.delenv("OMK_PROXY_MIN_ANONYMITY", raising=False)
        engine = _FakeEngine()
        engine.nodes = {
            "transparent": ProxyNode(url="3.3.3.3:80", anonymity="transparent", anonymity_probed=True),
            "elite": ProxyNode(url="2.2.2.2:80", anonymity="elite", anonymity_probed=True, integrity=True),
        }

        def real_next(exclude=None, require_integrity=False, min_anonymity=None):
            for n in engine.nodes.values():
                if exclude and n.url in exclude:
                    continue
                if require_integrity and n.integrity is not True:
                    continue
                if min_anonymity and (
                    engine_mod.ANONYMITY_RANK.get(n.anonymity, -1)
                    < engine_mod.ANONYMITY_RANK[min_anonymity]
                ):
                    continue
                return n
            return None

        engine.next = real_next
        calls = []

        class T:
            def fetch(self, url, **kw):
                calls.append(kw.get("proxy"))
                return CrawlResult(url=url, status=CrawlStatus.OK, html="ok")

        crawler = RotationCrawler(engine=engine, tool=T(), auto_refill=False)
        result = crawler.crawl("https://x.example/")
        assert result.ok
        assert all("3.3.3.3" not in (p or "") for p in calls)

    def test_env_off_accepts_transparent(self, monkeypatch):
        monkeypatch.setenv("OMK_PROXY_MIN_ANONYMITY", "off")
        crawler = RotationCrawler(engine=_FakeEngine(), tool=None, auto_refill=False)
        assert crawler.min_anonymity is None

    def test_env_elite(self, monkeypatch):
        monkeypatch.setenv("OMK_PROXY_MIN_ANONYMITY", "elite")
        crawler = RotationCrawler(engine=_FakeEngine(), tool=None, auto_refill=False)
        assert crawler.min_anonymity == "elite"
