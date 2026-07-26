"""iOS App Store public metadata (no IPA required).

Uses the public iTunes Lookup / Search API — sufficient when IPA is
unavailable (Apple DRM). Useful for iOS-only surface recon.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AppStoreApp:
    track_id: int
    track_name: str
    bundle_id: str
    version: str = ""
    seller_name: str = ""
    artist_name: str = ""
    minimum_os: str = ""
    file_size_bytes: int | None = None
    average_user_rating: float | None = None
    user_rating_count: int | None = None
    genres: list[str] = field(default_factory=list)
    price: float | None = None
    currency: str = ""
    track_view_url: str = ""
    artwork_url: str = ""
    screenshot_urls: list[str] = field(default_factory=list)
    description_head: str = ""
    release_date: str = ""
    current_version_release_date: str = ""
    content_advisory: str = ""
    language_codes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw", None)
        return d

    def to_markdown(self) -> str:
        size_mb = (
            f"{self.file_size_bytes / 1_000_000:.1f} MB"
            if self.file_size_bytes
            else "?"
        )
        lines = [
            f"# App Store: {self.track_name}",
            "",
            f"- trackId: `{self.track_id}`",
            f"- bundleId: `{self.bundle_id}`",
            f"- version: `{self.version}`",
            f"- seller: {self.seller_name or self.artist_name}",
            f"- min_os: `{self.minimum_os or '?'}`",
            f"- size: {size_mb}",
            f"- rating: {self.average_user_rating} ({self.user_rating_count} ratings)",
            f"- price: {self.price} {self.currency}",
            f"- genres: {', '.join(self.genres)}",
            f"- url: {self.track_view_url}",
            "",
            "## Description (head)",
            "",
            self.description_head or "_empty_",
            "",
        ]
        if self.screenshot_urls:
            lines += ["## Screenshots", ""]
            for u in self.screenshot_urls[:6]:
                lines.append(f"- {u}")
        return "\n".join(lines) + "\n"


def _from_result(a: dict[str, Any]) -> AppStoreApp:
    size = a.get("fileSizeBytes")
    try:
        size_i = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_i = None
    return AppStoreApp(
        track_id=int(a.get("trackId") or 0),
        track_name=str(a.get("trackName") or ""),
        bundle_id=str(a.get("bundleId") or ""),
        version=str(a.get("version") or ""),
        seller_name=str(a.get("sellerName") or ""),
        artist_name=str(a.get("artistName") or ""),
        minimum_os=str(a.get("minimumOsVersion") or ""),
        file_size_bytes=size_i,
        average_user_rating=(
            float(a["averageUserRating"]) if a.get("averageUserRating") is not None else None
        ),
        user_rating_count=(
            int(a["userRatingCount"]) if a.get("userRatingCount") is not None else None
        ),
        genres=list(a.get("genres") or []),
        price=float(a["price"]) if a.get("price") is not None else None,
        currency=str(a.get("currency") or ""),
        track_view_url=str(a.get("trackViewUrl") or ""),
        artwork_url=str(a.get("artworkUrl512") or a.get("artworkUrl100") or ""),
        screenshot_urls=list(a.get("screenshotUrls") or [])[:8],
        description_head=(str(a.get("description") or "")[:400]),
        release_date=str(a.get("releaseDate") or ""),
        current_version_release_date=str(a.get("currentVersionReleaseDate") or ""),
        content_advisory=str(a.get("contentAdvisoryRating") or ""),
        language_codes=list(a.get("languageCodesISO2A") or []),
        raw=a,
    )


class AppStoreClient:
    """Public iTunes Search/Lookup client."""

    def __init__(self, *, country: str = "kr", timeout: float = 20.0) -> None:
        self.country = country
        self.timeout = timeout

    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
            ),
            "Accept": "application/json",
        }
        last_err: Exception | None = None
        try:
            from curl_cffi import requests as cffi

            r = cffi.get(
                url,
                params=params,
                headers=headers,
                impersonate="chrome131",
                timeout=self.timeout,
            )
            if r.status_code < 400:
                return r.json()
            last_err = RuntimeError(f"itunes HTTP {r.status_code}")
        except Exception as exc:  # network / dns
            last_err = exc

        import urllib.error
        import urllib.parse
        import urllib.request

        q = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{q}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"App Store lookup failed: {exc}") from (last_err or exc)

    def lookup(self, track_id: int | str) -> AppStoreApp | None:
        data = self._get(
            "https://itunes.apple.com/lookup",
            {"id": str(track_id), "country": self.country},
        )
        results = data.get("results") or []
        if not results:
            return None
        return _from_result(results[0])

    def lookup_bundle(self, bundle_id: str) -> AppStoreApp | None:
        data = self._get(
            "https://itunes.apple.com/lookup",
            {"bundleId": bundle_id, "country": self.country},
        )
        results = data.get("results") or []
        if not results:
            return None
        return _from_result(results[0])

    def search(self, term: str, *, limit: int = 10, entity: str = "software") -> list[AppStoreApp]:
        data = self._get(
            "https://itunes.apple.com/search",
            {
                "term": term,
                "country": self.country,
                "entity": entity,
                "limit": str(limit),
            },
        )
        return [_from_result(r) for r in (data.get("results") or [])]

    def batch_lookup(self, track_ids: list[int | str]) -> list[AppStoreApp]:
        if not track_ids:
            return []
        data = self._get(
            "https://itunes.apple.com/lookup",
            {"id": ",".join(str(i) for i in track_ids), "country": self.country},
        )
        return [_from_result(r) for r in (data.get("results") or [])]
