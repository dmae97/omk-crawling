"""Cookie/session manager — use YOUR OWN legitimate login sessions.

LEGAL SCOPE: This loads cookies that you exported from a browser where YOU are
logged in with YOUR account, so you can automate access to content you are
already authorized to see (e.g. a private cafe you have joined, or login-gated
features your account can use). This is session reuse, NOT authentication
bypass.

This module does NOT forge, guess, steal, or bypass credentials. If you are not
a member of a private cafe, no cookie will grant access — and attempting to
fabricate a session is unauthorized access (정보통신망법 §48), which is out of
scope.

Supported import formats:
  - JSON list   : EditThisCookie / cookie-editor export  (list of {name,value,domain,...})
  - JSON dict   : {"NAVER_SESS": "...", "nid": "..."}
  - Netscape    : cookies.txt (curl / browser export)
  - Header str  : "NAVER_SESS=...; nid=...; ..."
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omk_crawl.stability import get_logger

log = get_logger("omk_crawl.cookies")


@dataclass
class Cookie:
    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: float | None = None     # epoch seconds; None = session cookie
    secure: bool = False
    http_only: bool = False

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires < time.time()

    def to_playwright(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "value": self.value,
                             "path": self.path or "/", "domain": self.domain}
        if self.expires:
            d["expires"] = self.expires
        return d


class CookieManager:
    """Load, validate, and inject your own session cookies."""

    def __init__(self) -> None:
        self.cookies: dict[str, Cookie] = {}

    # ── import ──────────────────────────────────────────────
    @classmethod
    def from_file(cls, path: str | Path) -> CookieManager:
        """Auto-detect format from a file (JSON or Netscape cookies.txt)."""
        mgr = cls()
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
        if text.startswith("[") or text.startswith("{"):
            mgr.load_json(text)
        else:
            mgr.load_netscape(text)
        return mgr

    def load_json(self, text: str, default_domain: str = "") -> CookieManager:
        data = json.loads(text)
        if isinstance(data, dict):
            for k, v in data.items():
                self.cookies[k] = Cookie(name=k, value=str(v), domain=default_domain)
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict) or "name" not in item:
                    continue
                exp = item.get("expirationDate") or item.get("expires")
                try:
                    exp = float(exp) if exp else None
                except (TypeError, ValueError):
                    exp = None
                self.cookies[item["name"]] = Cookie(
                    name=item["name"], value=str(item.get("value", "")),
                    domain=item.get("domain", default_domain),
                    path=item.get("path", "/"), expires=exp,
                    secure=bool(item.get("secure", False)),
                    http_only=bool(item.get("httpOnly", False)),
                )
        log.info("loaded %d cookies from JSON", len(self.cookies))
        return self

    def load_netscape(self, text: str) -> CookieManager:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _flag, path, secure, expires, name, value = parts[:7]
            try:
                exp = float(expires) if expires and expires != "0" else None
            except ValueError:
                exp = None
            self.cookies[name] = Cookie(
                name=name, value=value, domain=domain, path=path,
                expires=exp, secure=secure.upper() == "TRUE",
            )
        log.info("loaded %d cookies from Netscape file", len(self.cookies))
        return self

    def load_header(self, header: str, default_domain: str = "") -> CookieManager:
        """Parse a `Cookie:` header value: 'a=1; b=2'."""
        for pair in header.split(";"):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                self.cookies[k.strip()] = Cookie(
                    name=k.strip(), value=v.strip(), domain=default_domain)
        log.info("loaded %d cookies from header", len(self.cookies))
        return self

    # ── export / inject ─────────────────────────────────────
    def active(self) -> dict[str, str]:
        """Return {name: value} for non-expired cookies."""
        return {c.name: c.value for c in self.cookies.values() if not c.expired}

    def header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.active().items())

    def to_curl_cffi(self) -> dict[str, str]:
        return self.active()

    def to_playwright(self, domain_fallback: str = "") -> list[dict[str, Any]]:
        out = []
        for c in self.cookies.values():
            if c.expired:
                continue
            if not c.domain and domain_fallback:
                c = Cookie(**{**c.__dict__, "domain": domain_fallback})
            out.append(c.to_playwright())
        return out

    # ── diagnostics ─────────────────────────────────────────
    def report(self) -> dict[str, Any]:
        active = [c for c in self.cookies.values() if not c.expired]
        expired = [c for c in self.cookies.values() if c.expired]
        return {
            "total": len(self.cookies),
            "active": len(active),
            "expired": len(expired),
            "names": sorted(c.name for c in active),
            "expired_names": sorted(c.name for c in expired),
        }

    def __len__(self) -> int:
        return len(self.active())
