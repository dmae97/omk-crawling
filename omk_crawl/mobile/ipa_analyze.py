"""Static IPA surface extraction (zip + Info.plist + string scan)."""

from __future__ import annotations

import plistlib
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_URL_RE = re.compile(
    rb"https?://[a-zA-Z0-9][-a-zA-Z0-9@:%._+~#=]{1,256}"
    rb"(?:\.[a-zA-Z0-9()]{1,63})+[^\x00-\x1f\x7f\"'<>\\\s]{0,200}"
)
_HOST_RE = re.compile(
    rb"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    rb"(?:baemin|woowahan|smartbaedal|firebaseio|appspot|googleapis|"
    rb"kakao|naver|coupang|toss|yogiyo|kurly|apple|icloud|"
    rb"crashlytics|app-measurement|branch\.io)"
    rb"\.[a-zA-Z.]{2,24}"
)


@dataclass
class IpaReport:
    path: str
    bundle_id: str = ""
    bundle_name: str = ""
    version: str = ""
    build: str = ""
    min_os: str = ""
    executable: str = ""
    url_schemes: list[str] = field(default_factory=list)
    query_schemes: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list)
    has_ats_allows_arbitrary: bool = False
    ats_exception_domains: list[str] = field(default_factory=list)
    background_modes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    tool: str = "ipa-zip"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            f"# IPA report: `{Path(self.path).name}`",
            "",
            f"- bundle_id: `{self.bundle_id or '?'}`",
            f"- name: `{self.bundle_name or '?'}`",
            f"- version: `{self.version or '?'}` ({self.build or '?'})",
            f"- min_os: `{self.min_os or '?'}`",
            f"- executable: `{self.executable or '?'}`",
            f"- ATS allows arbitrary loads: {self.has_ats_allows_arbitrary}",
            f"- frameworks: {len(self.frameworks)}",
            f"- extensions: {len(self.extensions)}",
            f"- analyzer: {self.tool}",
            "",
            f"## URL schemes ({len(self.url_schemes)})",
        ]
        for s in self.url_schemes[:40]:
            lines.append(f"- `{s}`")
        if self.query_schemes:
            lines += ["", f"## LSApplicationQueriesSchemes ({len(self.query_schemes)})"]
            for s in self.query_schemes[:40]:
                lines.append(f"- `{s}`")
        if self.ats_exception_domains:
            lines += ["", f"## ATS exception domains ({len(self.ats_exception_domains)})"]
            for d in self.ats_exception_domains[:40]:
                lines.append(f"- `{d}`")
        if self.background_modes:
            lines += ["", f"## Background modes ({len(self.background_modes)})"]
            for m in self.background_modes:
                lines.append(f"- `{m}`")
        if self.hosts:
            lines += ["", f"## Hosts ({len(self.hosts)})"]
            for h in self.hosts[:80]:
                lines.append(f"- `{h}`")
        if self.urls:
            lines += ["", f"## URLs ({len(self.urls)})"]
            for u in self.urls[:120]:
                lines.append(f"- {u}")
        if self.frameworks:
            lines += ["", f"## Frameworks ({len(self.frameworks)})"]
            for f in self.frameworks[:40]:
                lines.append(f"- `{f}`")
        if self.extensions:
            lines += ["", f"## App extensions ({len(self.extensions)})"]
            for e in self.extensions[:20]:
                lines.append(f"- `{e}`")
        if self.errors:
            lines += ["", "## Errors"]
            for e in self.errors:
                lines.append(f"- {e}")
        return "\n".join(lines) + "\n"


def _find_info_plist(names: list[str]) -> str | None:
    cands = [n for n in names if n.endswith(".app/Info.plist") and n.startswith("Payload/")]
    # prefer shallowest main app plist (not appex)
    cands = [c for c in cands if ".appex/" not in c]
    return sorted(cands, key=len)[0] if cands else None


def _clean_url(u: str) -> str | None:
    u = u.rstrip(").,;:'\"\\]>}")
    if len(u) < 12 or len(u) > 300:
        return None
    low = u.lower()
    if any(
        n in low
        for n in (
            "dtd",
            "schemas.android",
            "www.w3.org",
            "apple.com/DTDs",
            "example.com/some",
        )
    ):
        # keep apple.com non-DTD
        if "dtd" in low or "schemas" in low:
            return None
    return u


def analyze_ipa(path: str | Path) -> IpaReport:
    ipa = Path(path).expanduser().resolve()
    report = IpaReport(path=str(ipa))
    if not ipa.is_file():
        report.errors.append(f"not a file: {ipa}")
        return report

    try:
        with zipfile.ZipFile(ipa, "r") as zf:
            names = zf.namelist()
            # Framework folder names
            fws: set[str] = set()
            for n in names:
                if ".framework/" in n:
                    part = n.split(".framework/")[0].split("/")[-1] + ".framework"
                    fws.add(part)
            report.frameworks = sorted(fws)[:80]

            # App extensions
            report.extensions = sorted(
                {
                    n.split("/PlugIns/")[-1].split("/")[0]
                    for n in names
                    if "/PlugIns/" in n and n.endswith(".appex/")
                }
            )[:40]

            plist_path = _find_info_plist(names)
            if plist_path:
                try:
                    info = plistlib.loads(zf.read(plist_path))
                    report.bundle_id = str(info.get("CFBundleIdentifier") or "")
                    report.bundle_name = str(
                        info.get("CFBundleDisplayName") or info.get("CFBundleName") or ""
                    )
                    report.version = str(info.get("CFBundleShortVersionString") or "")
                    report.build = str(info.get("CFBundleVersion") or "")
                    report.min_os = str(info.get("MinimumOSVersion") or "")
                    report.executable = str(info.get("CFBundleExecutable") or "")
                    ats = info.get("NSAppTransportSecurity") or {}
                    if isinstance(ats, dict):
                        report.has_ats_allows_arbitrary = bool(ats.get("NSAllowsArbitraryLoads"))
                        exc = ats.get("NSExceptionDomains") or {}
                        if isinstance(exc, dict):
                            report.ats_exception_domains = sorted(str(k) for k in exc.keys())
                    schemes: list[str] = []
                    for entry in info.get("CFBundleURLTypes") or []:
                        if isinstance(entry, dict):
                            for s in entry.get("CFBundleURLSchemes") or []:
                                schemes.append(str(s))
                    report.url_schemes = schemes
                    qs = info.get("LSApplicationQueriesSchemes") or []
                    if isinstance(qs, list):
                        report.query_schemes = [str(x) for x in qs]
                    bg = info.get("UIBackgroundModes") or []
                    if isinstance(bg, list):
                        report.background_modes = [str(x) for x in bg]
                except Exception as exc:
                    report.errors.append(f"plist parse: {exc}")
            else:
                report.errors.append("Info.plist not found under Payload/")

            # String scan: main binary + plists + small frameworks
            blob = b""
            app_prefix = ""
            if plist_path:
                app_prefix = plist_path.rsplit("/", 1)[0] + "/"

            scan_names: list[str] = []
            if report.executable and app_prefix:
                scan_names.append(app_prefix + report.executable)
            for n in names:
                if n.endswith((".plist", ".js", ".json", ".dylib", ".car")):
                    scan_names.append(n)
                elif (
                    app_prefix
                    and n.startswith(app_prefix)
                    and n.count("/") <= app_prefix.count("/") + 1
                ):
                    # top-level bundle files
                    if not n.endswith("/"):
                        scan_names.append(n)

            seen: set[str] = set()
            for n in scan_names:
                if n in seen:
                    continue
                seen.add(n)
                try:
                    data = zf.read(n)
                except Exception:
                    continue
                # skip huge
                if len(data) > 25_000_000:
                    data = data[:25_000_000]
                blob += data[:2_000_000] + b"\n"
                if len(blob) > 12_000_000:
                    break

            urls: set[str] = set()
            hosts: set[str] = set()
            for m in _URL_RE.findall(blob):
                try:
                    u = m.decode("utf-8", "ignore")
                except Exception:
                    continue
                cu = _clean_url(u)
                if cu:
                    urls.add(cu)
            for m in _HOST_RE.findall(blob):
                try:
                    h = m.decode("utf-8", "ignore").lower().lstrip(".-")
                except Exception:
                    continue
                if 6 <= len(h) <= 120:
                    hosts.add(h)
            report.urls = sorted(urls)[:300]
            report.hosts = sorted(hosts)[:200]
    except zipfile.BadZipFile as exc:
        report.errors.append(f"BadZipFile: {exc}")
    return report
