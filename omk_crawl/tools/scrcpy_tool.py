"""Android device bridge — adb inventory + optional scrcpy session record.

Not a web crawler. Surfaces mobile-only data paths when HTTP is impossible.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from omk_crawl.mobile.device import adb_shell, list_adb_devices, which_bin
from omk_crawl.result import CrawlResult, CrawlStatus
from omk_crawl.tools.base import BaseTool


def _parse_android_url(url: str) -> dict[str, str]:
    """android://serial[/action]?pkg=… → parts."""
    raw = url.strip()
    if raw in {"android://", "android:///", "adb://", "device://"}:
        return {"action": "list"}
    if raw.startswith("adb://"):
        raw = "android://" + raw[len("adb://") :]
    if not raw.startswith("android://"):
        return {"action": "list"}
    u = urlparse(raw)
    serial = unquote(u.netloc or "")
    action = (u.path or "/").strip("/") or "list"
    qs = parse_qs(u.query or "")
    out = {"action": action, "serial": serial}
    for k, v in qs.items():
        if v:
            out[k] = v[0]
    return out


class ScrcpyTool(BaseTool):
    """ADB device list / package dump / activity snapshot."""

    name = "scrcpy"
    pip_package = ""  # system: adb, optional scrcpy
    layer = 5
    needs_browser = False
    capabilities: frozenset[str] = frozenset({"timeout"})

    def available(self) -> bool:
        return which_bin("adb") is not None

    def install_hint(self) -> str:
        return "Install Android platform-tools (adb) and optionally: apt/brew install scrcpy"

    def fetch(self, url: str, **kwargs: Any) -> CrawlResult:
        t0 = time.perf_counter()
        meta = self.contract_metadata(kwargs)
        if not self.available():
            return self._missing(url)

        parts = _parse_android_url(url)
        action = parts.get("action", "list")
        devices = list_adb_devices()
        online = [d for d in devices if d.online]

        if action in {"", "list", "devices"}:
            lines = ["# Android devices", ""]
            if not devices:
                lines.append("_No devices. Enable USB debugging and run `adb devices`._")
            for d in devices:
                mark = "online" if d.online else d.state
                lines.append(f"- `{d.serial}` · {mark} · {d.model or d.product or '?'}")
            md = "\n".join(lines) + "\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                markdown=md,
                fit_markdown=md,
                extracted=[
                    {"serial": d.serial, "state": d.state, "model": d.model}
                    for d in devices
                ],
                tool=self.name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={
                    **meta,
                    "device_count": len(devices),
                    "scrcpy": bool(which_bin("scrcpy")),
                },
            )

        serial = parts.get("serial") or (online[0].serial if online else "")
        if not serial:
            return CrawlResult(
                url=url,
                status=CrawlStatus.ERROR,
                tool=self.name,
                error="No online adb device. Connect a device or pass android://SERIAL/…",
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata=meta,
            )

        if action in {"packages", "pkgs"}:
            code, out, err = adb_shell(serial, "pm", "list", "packages", "-3")
            pkgs = sorted(
                line.replace("package:", "").strip()
                for line in out.splitlines()
                if line.startswith("package:")
            )
            md = "# Third-party packages\n\n" + "\n".join(f"- `{p}`" for p in pkgs[:500]) + "\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK if code == 0 else CrawlStatus.ERROR,
                status_code=200 if code == 0 else None,
                markdown=md,
                fit_markdown=md,
                extracted=[{"packages": pkgs}],
                tool=self.name,
                error=err.strip() or None if code else None,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "serial": serial, "count": len(pkgs)},
            )

        if action in {"dump", "pkg"}:
            pkg = parts.get("pkg") or parts.get("package")
            if not pkg:
                return CrawlResult(
                    url=url,
                    status=CrawlStatus.ERROR,
                    tool=self.name,
                    error="Missing pkg= query (android://SERIAL/dump?pkg=com.example)",
                    elapsed_ms=(time.perf_counter() - t0) * 1000,
                    metadata=meta,
                )
            code, out, err = adb_shell(serial, "dumpsys", "package", pkg, timeout=45.0)
            # keep dumpsys manageable
            text = out[:200_000]
            md = f"# dumpsys package {pkg}\n\n```\n{text}\n```\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK if code == 0 and text else CrawlStatus.ERROR,
                status_code=200 if code == 0 else None,
                markdown=md,
                fit_markdown=text,
                tool=self.name,
                error=(err or "empty dumpsys") if code else None,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "serial": serial, "pkg": pkg},
            )

        if action == "activity":
            code, out, err = adb_shell(
                serial, "dumpsys", "activity", "activities", timeout=30.0
            )
            text = out[:120_000]
            md = "# Current activities\n\n```\n" + text + "\n```\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK if code == 0 else CrawlStatus.ERROR,
                markdown=md,
                fit_markdown=text,
                tool=self.name,
                error=err or None if code else None,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "serial": serial},
            )

        if action == "screenshot":
            out_path = parts.get("out") or str(Path.cwd() / "omk_android_screen.png")
            import subprocess

            adb = which_bin("adb")
            if not adb:
                return self._missing(url)
            try:
                proc = subprocess.run(
                    [adb, "-s", serial, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=float(kwargs.get("timeout") or 30),
                    check=False,
                )
                if proc.returncode != 0 or not proc.stdout:
                    return CrawlResult(
                        url=url,
                        status=CrawlStatus.ERROR,
                        tool=self.name,
                        error=(proc.stderr or b"").decode("utf-8", "ignore") or "screencap failed",
                        elapsed_ms=(time.perf_counter() - t0) * 1000,
                        metadata=meta,
                    )
                Path(out_path).write_bytes(proc.stdout)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return self._error(url, exc)
            md = f"# Screenshot\n\nSaved `{out_path}` ({len(proc.stdout)} bytes)\n"
            return CrawlResult(
                url=url,
                status=CrawlStatus.OK,
                status_code=200,
                markdown=md,
                fit_markdown=md,
                tool=self.name,
                elapsed_ms=(time.perf_counter() - t0) * 1000,
                metadata={**meta, "serial": serial, "path": out_path, "bytes": len(proc.stdout)},
            )

        return CrawlResult(
            url=url,
            status=CrawlStatus.ERROR,
            tool=self.name,
            error=(
                f"Unknown android action '{action}'. "
                "Use list|packages|dump|activity|screenshot"
            ),
            elapsed_ms=(time.perf_counter() - t0) * 1000,
            metadata={**meta, "hint": json.dumps(parts)},
        )
