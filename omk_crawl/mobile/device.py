"""ADB / host binary discovery for Android devices."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass


def which_bin(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


@dataclass(frozen=True)
class AdbDevice:
    serial: str
    state: str
    model: str = ""
    product: str = ""

    @property
    def online(self) -> bool:
        return self.state == "device"


_PROP_RE = re.compile(r"(\S+):(\S+)")


def list_adb_devices(*, adb: str | None = None, timeout: float = 8.0) -> list[AdbDevice]:
    """Parse `adb devices -l`. Empty list if adb missing / no devices."""
    bin_path = adb or which_bin("adb")
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    out: list[AdbDevice] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        props = dict(_PROP_RE.findall(line))
        out.append(
            AdbDevice(
                serial=serial,
                state=state,
                model=props.get("model", "").replace("_", " "),
                product=props.get("product", ""),
            )
        )
    return out


def adb_shell(
    serial: str,
    *args: str,
    adb: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, str, str]:
    bin_path = adb or which_bin("adb")
    if not bin_path:
        return 127, "", "adb not found"
    cmd = [bin_path, "-s", serial, "shell", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
