"""Mobile surface tests (no device required)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from omk_crawl.mobile.apk_analyze import analyze_apk
from omk_crawl.mobile.device import which_bin
from omk_crawl.mobile.ipa_analyze import analyze_ipa
from omk_crawl.tools import ALL_TOOLS, MOBILE_TOOLS, get_tool
from omk_crawl.tools.apk_tool import ApkTool
from omk_crawl.tools.ipa_tool import IpaTool
from omk_crawl.tools.scrcpy_tool import ScrcpyTool, _parse_android_url


def _minimal_apk(path: Path) -> None:
    """Write a tiny zip that looks enough like an APK for string scan."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classes.dex", b"dex\nhttps://api.example.com/v1/users\nfirebaseio.com\n")
        zf.writestr(
            "assets/config.json",
            b'{"endpoint":"https://cdn.example.com/app","graphql":"/graphql"}',
        )
        zf.writestr("lib/arm64-v8a/libnative.so", b"\x00ELF")
        zf.writestr("AndroidManifest.xml", b"not-real-binary-xml")
    path.write_bytes(buf.getvalue())


def _minimal_ipa(path: Path) -> None:
    import plistlib

    info = {
        "CFBundleIdentifier": "com.example.demo",
        "CFBundleName": "Demo",
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "99",
        "MinimumOSVersion": "15.0",
        "CFBundleExecutable": "Demo",
        "CFBundleURLTypes": [{"CFBundleURLSchemes": ["demodemo"]}],
        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Payload/Demo.app/Info.plist", plistlib.dumps(info))
        zf.writestr(
            "Payload/Demo.app/Frameworks/Foo.framework/",
            b"",
        )
        zf.writestr(
            "Payload/Demo.app/assets.js",
            b"const API='https://api.ios.example.com/v2';",
        )
    path.write_bytes(buf.getvalue())


def test_mobile_tools_registered() -> None:
    assert "apk" in ALL_TOOLS
    assert "ipa" in ALL_TOOLS
    assert "scrcpy" in ALL_TOOLS
    assert MOBILE_TOOLS >= {"apk", "ipa", "scrcpy"}


def test_apk_analyze_strings(tmp_path: Path) -> None:
    apk = tmp_path / "t.apk"
    _minimal_apk(apk)
    rep = analyze_apk(apk)
    assert rep.dex_count >= 1
    assert any("example.com" in u for u in rep.urls)
    assert rep.to_markdown().startswith("# APK")


def test_apk_tool_fetch(tmp_path: Path) -> None:
    apk = tmp_path / "t.apk"
    _minimal_apk(apk)
    r = ApkTool().fetch(str(apk))
    assert r.ok
    assert r.tool == "apk"
    assert r.markdown and "APK report" in r.markdown


def test_ipa_analyze(tmp_path: Path) -> None:
    ipa = tmp_path / "t.ipa"
    _minimal_ipa(ipa)
    rep = analyze_ipa(ipa)
    assert rep.bundle_id == "com.example.demo"
    assert rep.version == "1.2.3"
    assert "demodemo" in rep.url_schemes
    assert rep.has_ats_allows_arbitrary
    assert any("ios.example.com" in u for u in rep.urls)


def test_ipa_tool_fetch(tmp_path: Path) -> None:
    ipa = tmp_path / "t.ipa"
    _minimal_ipa(ipa)
    r = IpaTool().fetch(str(ipa))
    assert r.ok
    assert r.metadata.get("bundle_id") == "com.example.demo"


def test_android_url_parse() -> None:
    assert _parse_android_url("android://")["action"] == "list"
    p = _parse_android_url("android://emulator-5554/dump?pkg=com.foo")
    assert p["serial"] == "emulator-5554"
    assert p["action"] == "dump"
    assert p["pkg"] == "com.foo"


def test_scrcpy_available_matches_adb() -> None:
    t = ScrcpyTool()
    assert t.available() is bool(which_bin("adb"))


def test_get_tool_mobile() -> None:
    assert isinstance(get_tool("apk"), ApkTool)
    assert isinstance(get_tool("ipa"), IpaTool)


def test_appstore_tool_registered() -> None:
    from omk_crawl.tools.appstore_tool import AppStoreTool, _parse_appstore_url

    assert "appstore" in ALL_TOOLS
    assert isinstance(get_tool("appstore"), AppStoreTool)
    p = _parse_appstore_url("appstore://378084485")
    assert p["action"] == "lookup"
    assert p["id"] == "378084485"
    p2 = _parse_appstore_url("appstore://search?q=요기요")
    assert p2["action"] == "search"
    assert "요기요" in p2.get("q", "")


def test_appstore_client_unit() -> None:
    from omk_crawl.mobile.appstore import AppStoreApp, _from_result

    app = _from_result(
        {
            "trackId": 1,
            "trackName": "Demo",
            "bundleId": "com.example.demo",
            "version": "1.0",
            "fileSizeBytes": "1000000",
            "averageUserRating": 4.5,
            "userRatingCount": 10,
            "genres": ["Food"],
            "price": 0.0,
            "currency": "KRW",
            "trackViewUrl": "https://apps.apple.com/kr/app/id1",
            "description": "hello world " * 50,
        }
    )
    assert isinstance(app, AppStoreApp)
    assert app.bundle_id == "com.example.demo"
    assert "Demo" in app.to_markdown()
    assert app.file_size_bytes == 1_000_000
