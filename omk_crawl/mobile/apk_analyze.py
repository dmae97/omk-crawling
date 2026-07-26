"""Static APK surface extraction (no install required).

Prefers `aapt`/`aapt2` when present; falls back to zip + binary AXML heuristics
and optional androguard if installed.
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from omk_crawl.mobile.device import which_bin

# Prefer printable URL-ish spans (dex embeds raw UTF-8)
_URL_RE = re.compile(
    rb"https?://[a-zA-Z0-9][-a-zA-Z0-9@:%._+~#=]{1,256}"
    rb"(?:\.[a-zA-Z0-9()]{1,63})+[^\x00-\x1f\x7f\"'<>\\\s]{0,200}"
)
# Real DNS hosts only (must look like domain.tld, not Java packages)
_HOST_RE = re.compile(
    rb"(?:https?://)?((?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    rb"(?:baemin|woowahan|smartbaedal|firebaseio|appspot|googleapis|"
    rb"kakao|naver|coupang|toss|yogiyo|kurly|apple|icloud|telegram|"
    rb"mozilla|firefox|fdroid|stripe|branch)\."
    rb"(?:com|net|org|io|co\.kr|kr|app)(?:\.[a-z]{2})?)"
    rb"(?=[^a-zA-Z0-9.-]|$)"
)
_API_HINT_RE = re.compile(
    rb"(?:/api/v\d+/|/v\d+/[a-zA-Z0-9_./-]{3,80}|/graphql|"
    rb"firebaseio\.com|googleapis\.com|appspot\.com|"
    rb"cloudfunctions\.net|amazonaws\.com|"
    rb"[a-z0-9.-]+\.baemin\.com|[a-z0-9.-]+\.woowahan\.com|"
    rb"[a-z0-9.-]+\.smartbaedal\.com)",
    re.I,
)
_PKG_RE = re.compile(rb"\b([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z][a-zA-Z0-9_]*){2,})\b")
_PERM_RE = re.compile(rb"android\.permission\.[A-Z0-9_]+")

_NOISE_URL = (
    "github.com/",
    "gnu.org",
    "apache.org",
    "opensource.org",
    "boost.org",
    "unicode.org",
    "sourceforge.net",
    "unlicense.org",
    "sqlite.org",
    "rapidjson",
    "mapbox/",
    "jetbrains/kotlin",
    "nlohmann.me",
    "dlib.net",
    "paulbourke.net",
    "angusj.com",
    "aist-nara.ac.jp",
    "uiuc.edu",
    "icu.unicode",
    "proj.org",
    "curl.haxx",
    "android.googlesource",
    "chromium.googlesource",
    "schemas.android.com",
    "www.w3.org",
    "xmlns",
    "junit.org",
    "jspecify.org",
    "creativecommons.org",
    "purl.org",
    "inkscape.org",
)


@dataclass
class ApkReport:
    path: str
    package: str = ""
    version_name: str = ""
    version_code: str = ""
    min_sdk: str = ""
    target_sdk: str = ""
    permissions: list[str] = field(default_factory=list)
    activities: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    receivers: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    api_hints: list[str] = field(default_factory=list)
    dex_count: int = 0
    native_libs: list[str] = field(default_factory=list)
    has_firebase: bool = False
    has_ssl_pinning_hints: bool = False
    tool: str = ""
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# APK report: `{Path(self.path).name}`",
            "",
            f"- package: `{self.package or '?'}`",
            f"- version: `{self.version_name or '?'}` ({self.version_code or '?'})",
            f"- sdk: min={self.min_sdk or '?'} target={self.target_sdk or '?'}",
            f"- dex: {self.dex_count}",
            f"- firebase: {self.has_firebase}",
            f"- ssl-pinning hints: {self.has_ssl_pinning_hints}",
            f"- analyzer: {self.tool or 'builtin'}",
            "",
            f"## Permissions ({len(self.permissions)})",
        ]
        for p in self.permissions[:80]:
            lines.append(f"- `{p}`")
        if self.hosts:
            lines += ["", f"## Hosts ({len(self.hosts)})"]
            for h in self.hosts[:80]:
                lines.append(f"- `{h}`")
        if self.urls:
            lines += ["", f"## URLs ({len(self.urls)})"]
            for u in self.urls[:120]:
                lines.append(f"- {u}")
        if self.api_hints:
            lines += ["", f"## API hints ({len(self.api_hints)})"]
            for h in self.api_hints[:100]:
                lines.append(f"- `{h}`")
        if self.native_libs:
            lines += ["", f"## Native libs ({len(self.native_libs)})"]
            for n in self.native_libs[:40]:
                lines.append(f"- `{n}`")
        if self.errors:
            lines += ["", "## Errors"]
            for e in self.errors:
                lines.append(f"- {e}")
        return "\n".join(lines) + "\n"


def _run(cmd: list[str], timeout: float = 30.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _aapt_dump(apk: Path) -> dict[str, Any]:
    aapt = which_bin("aapt2", "aapt")
    if not aapt:
        return {}
    # aapt dump badging | aapt2 dump badging
    for args in ([aapt, "dump", "badging", str(apk)], [aapt, "dump", "badging", str(apk)]):
        code, out = _run(args)
        if code == 0 and "package:" in out:
            break
    else:
        return {"error": out[:300] if "out" in dir() else "aapt failed"}

    data: dict[str, Any] = {"raw": out}
    m = re.search(r"package: name='([^']+)'", out)
    if m:
        data["package"] = m.group(1)
    m = re.search(r"versionName='([^']*)'", out)
    if m:
        data["version_name"] = m.group(1)
    m = re.search(r"versionCode='([^']*)'", out)
    if m:
        data["version_code"] = m.group(1)
    m = re.search(r"sdkVersion:'([^']*)'", out)
    if m:
        data["min_sdk"] = m.group(1)
    m = re.search(r"targetSdkVersion:'([^']*)'", out)
    if m:
        data["target_sdk"] = m.group(1)
    data["permissions"] = re.findall(r"uses-permission: name='([^']+)'", out)
    data["activities"] = re.findall(r"launchable-activity: name='([^']+)'", out)
    return data


def _axml_strings(data: bytes) -> list[str]:
    """Best-effort binary Android XML string pool reader."""
    if len(data) < 16 or data[:2] != b"\x03\x00":
        return []
    # Scan UTF-16LE / UTF-8 printable runs inside pool (robust vs full AXML parse)
    out: list[str] = []
    # UTF-16LE runs
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,120}", data):
        try:
            s = m.group().decode("utf-16-le")
        except Exception:
            continue
        out.append(s)
    # UTF-8 runs
    for m in re.finditer(rb"[\x20-\x7e]{4,160}", data):
        out.append(m.group().decode("ascii", "ignore"))
    return out


_KNOWN_APP_IDS = (
    "com.sampleapp",  # Baemin Android applicationId
    "com.jawebs.baedal",  # Baemin iOS bundle (cross-ref)
    "org.telegram.messenger",
    "org.telegram.messenger.web",
    "com.termux",
    "org.fdroid.fdroid",
    "org.mozilla.fennec_fdroid",
    "org.mozilla.firefox",
    "com.android.chrome",
)


def _manifest_meta(data: bytes) -> dict[str, Any]:
    strings = _axml_strings(data)
    meta: dict[str, Any] = {
        "permissions": [],
        "package": "",
        "version_name": "",
        "version_code": "",
        "min_sdk": "",
        "target_sdk": "",
        "activities": [],
    }
    perms: set[str] = set()
    str_set = set(strings)
    for s in strings:
        if re.fullmatch(r"android\.permission\.[A-Z0-9_]+", s):
            perms.add(s)
        elif ".permission." in s and s.startswith("com."):
            perms.add(s)
    for m in _PERM_RE.findall(data):
        perms.add(m.decode("ascii", "ignore"))
    meta["permissions"] = sorted(perms)

    # Prefer exact known applicationIds present in string pool
    for kid in _KNOWN_APP_IDS:
        if kid in str_set or kid.encode() in data:
            meta["package"] = kid
            break
    if not meta["package"]:
        # short package-like tokens only (2–3 segments), exclude class paths
        cands: list[str] = []
        for s in strings:
            if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,3}", s):
                continue
            if s.startswith(("com.google.", "com.android.", "androidx.", "kotlin.", "java.")):
                continue
            if any(x in s for x in ("firebase", "gms.", "crashlytics", "appcompat")):
                continue
            # reject CamelCase class tails
            tail = s.rsplit(".", 1)[-1]
            if tail[:1].isupper():
                continue
            if s.count(".") >= 1 and len(s) < 60:
                cands.append(s)
        # prefer tokens that look like app ids (com.* / org.*)
        def _pkg_rank(x: str) -> tuple[int, int]:
            return (0 if x.startswith(("com.", "org.")) else 1, len(x))

        cands = sorted(set(cands), key=_pkg_rank)
        if cands:
            meta["package"] = cands[0]

    # version name: prefer X.Y.Z present near package
    for s in strings:
        if re.fullmatch(r"\d+\.\d+\.\d+", s):
            meta["version_name"] = s
            break
    return meta


def _clean_url(u: str) -> str | None:
    u = u.rstrip(").,;:'\"\\]>}")
    # trim trailing junk path garbage from dex
    if len(u) < 12 or len(u) > 300:
        return None
    low = u.lower()
    if any(n in low for n in _NOISE_URL):
        return None
    if " " in u or "\n" in u:
        return None
    return u


def _scan_zip_strings(apk: Path) -> tuple[list[str], list[str], list[str], dict[str, Any]]:
    urls: set[str] = set()
    hosts: set[str] = set()
    hints: set[str] = set()
    meta: dict[str, Any] = {
        "dex_count": 0,
        "native_libs": [],
        "has_firebase": False,
        "has_ssl_pinning_hints": False,
        "manifest": {},
    }
    pin_markers = (
        b"CertificatePinner",
        b"ssl pinning",
        b"TrustManager",
        b"okhttp3",
        b"network_security_config",
    )
    try:
        with zipfile.ZipFile(apk, "r") as zf:
            names = zf.namelist()
            meta["dex_count"] = sum(
                1 for n in names if re.fullmatch(r"classes\d*\.dex", n.split("/")[-1] or "")
            )
            # also count root classes*.dex only for report consistency
            meta["dex_count"] = sum(
                1 for n in names if n == "classes.dex" or re.fullmatch(r"classes\d+\.dex", n)
            )
            meta["native_libs"] = sorted(
                {n.split("/")[-1] for n in names if n.startswith("lib/") and n.endswith(".so")}
            )[:60]

            if "AndroidManifest.xml" in names:
                try:
                    man = zf.read("AndroidManifest.xml")
                    meta["manifest"] = _manifest_meta(man)
                    if any(m in man for m in pin_markers):
                        meta["has_ssl_pinning_hints"] = True
                except Exception as exc:
                    meta["manifest_error"] = str(exc)[:120]

            # Priority: all root dex first, then config files, then assets (capped)
            dex_files = [
                n
                for n in names
                if n == "classes.dex" or re.fullmatch(r"classes\d+\.dex", n)
            ]
            dex_files = sorted(
                dex_files,
                key=lambda n: 0 if n == "classes.dex" else int(re.search(r"\d+", n).group())  # type: ignore[union-attr]
                if re.search(r"\d+", n)
                else 99,
            )
            config_files = [
                n
                for n in names
                if n.endswith(
                    (
                        ".json",
                        ".properties",
                        ".js",
                        ".xml",
                        ".txt",
                        ".cfg",
                        ".conf",
                    )
                )
                and not n.startswith("res/")
            ][:40]
            asset_files = [n for n in names if n.startswith("assets/") and not n.endswith("/")][
                :30
            ]
            priority = dex_files + config_files + asset_files

            for n in priority:
                try:
                    data = zf.read(n)
                except Exception:
                    continue
                if any(m in data for m in pin_markers):
                    meta["has_ssl_pinning_hints"] = True
                low = data[:2_000_000].lower()
                if b"firebase" in low or b"google-services" in low:
                    meta["has_firebase"] = True

                # Scan in 2MB windows for large dex
                size = len(data)
                step = 1_800_000
                offset = 0
                while offset < size:
                    chunk = data[offset : offset + 2_000_000]
                    offset += step
                    for m in _URL_RE.findall(chunk):
                        try:
                            u = m.decode("utf-8", "ignore")
                        except Exception:
                            continue
                        cu = _clean_url(u)
                        if cu:
                            urls.add(cu)
                    for m in _HOST_RE.findall(chunk):
                        try:
                            # group 1 = host when pattern has capture
                            raw = m[0] if isinstance(m, tuple) else m
                            h = raw.decode("utf-8", "ignore").lower().lstrip(".-")
                            h = h.removeprefix("https://").removeprefix("http://")
                        except Exception:
                            continue
                        if not (6 <= len(h) <= 120) or " " in h:
                            continue
                        # strip leading junk digits glued from dex (e.g. 0type.googleapis.com)
                        h = re.sub(r"^[^a-z]+", "", h)
                        if not h or h[0].isdigit():
                            continue
                        if any(c.isupper() for c in h):
                            continue
                        if re.search(r"[^a-z0-9.-]", h):
                            continue
                        # must end with real tld
                        if not re.search(
                            r"\.(?:com|net|org|io|app|kr|co\.kr)$",
                            h,
                        ):
                            continue
                        # reject single-label garbage
                        if h.count(".") < 1:
                            continue
                        hosts.add(h)
                    for m in _API_HINT_RE.findall(chunk):
                        try:
                            hints.add(m.decode("utf-8", "ignore")[:160])
                        except Exception:
                            pass
                    if offset > 12_000_000:
                        # cap per-file
                        break
    except zipfile.BadZipFile as exc:
        meta["error"] = f"BadZipFile: {exc}"

    # Prefer app/API urls first
    def url_rank(u: str) -> tuple[int, str]:
        low = u.lower()
        score = 0
        if any(x in low for x in ("baemin", "woowahan", "smartbaedal", "api.", "/v1/", "/v2/")):
            score -= 10
        if low.startswith("https://"):
            score -= 1
        if any(x in low for x in (".png", ".jpg", ".gif", ".css", ".svg")):
            score += 5
        return (score, u)

    return (
        sorted(urls, key=url_rank)[:400],
        sorted(hosts)[:200],
        sorted(hints)[:200],
        meta,
    )


def analyze_apk(path: str | Path) -> ApkReport:
    apk = Path(path).expanduser().resolve()
    report = ApkReport(path=str(apk))
    if not apk.is_file():
        report.errors.append(f"not a file: {apk}")
        return report
    if apk.suffix.lower() not in {".apk", ".xapk", ".apks"}:
        report.errors.append(f"unexpected suffix: {apk.suffix}")

    aapt = _aapt_dump(apk)
    if aapt.get("package"):
        report.tool = "aapt"
        report.package = str(aapt.get("package", ""))
        report.version_name = str(aapt.get("version_name", ""))
        report.version_code = str(aapt.get("version_code", ""))
        report.min_sdk = str(aapt.get("min_sdk", ""))
        report.target_sdk = str(aapt.get("target_sdk", ""))
        report.permissions = list(aapt.get("permissions") or [])
        report.activities = list(aapt.get("activities") or [])
    elif aapt.get("error"):
        report.errors.append(str(aapt["error"])[:200])

    urls, hosts, hints, meta = _scan_zip_strings(apk)
    report.urls = urls
    report.hosts = hosts
    report.api_hints = hints
    report.dex_count = int(meta.get("dex_count") or 0)
    report.native_libs = list(meta.get("native_libs") or [])
    report.has_firebase = bool(meta.get("has_firebase"))
    report.has_ssl_pinning_hints = bool(meta.get("has_ssl_pinning_hints"))
    if meta.get("error"):
        report.errors.append(str(meta["error"]))

    man = meta.get("manifest") or {}
    if not report.package and man.get("package"):
        report.package = str(man["package"])
    if not report.version_name and man.get("version_name"):
        report.version_name = str(man["version_name"])
    if not report.permissions and man.get("permissions"):
        report.permissions = list(man["permissions"])
    elif man.get("permissions"):
        report.permissions = sorted(set(report.permissions) | set(man["permissions"]))

    if not report.tool:
        report.tool = "zip-scan"
    elif "zip" not in report.tool:
        report.tool = f"{report.tool}+zip-scan"

    # Optional androguard enrichment
    try:
        from androguard.misc import AnalyzeAPK  # type: ignore

        a, _, _ = AnalyzeAPK(str(apk))
        report.tool = (report.tool + "+androguard").replace("++", "+")
        report.package = report.package or (a.get_package() or "")
        report.permissions = sorted(set(report.permissions) | set(a.get_permissions() or []))
        report.activities = sorted(set(report.activities) | set(a.get_activities() or []))
        report.services = sorted(set(a.get_services() or []))
        report.receivers = sorted(set(a.get_receivers() or []))
    except Exception:
        pass

    return report
