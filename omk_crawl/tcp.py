"""TCP/IP stack emulation — the layer below TLS, and the one most clients miss.

Before TLS there is a SYN. Its parameters (TTL, advertised window, MSS, option
order, timestamps, DF) form an OS signature that passive fingerprinter such as
p0f read without touching the application. A client whose User-Agent claims
Windows while its stack is unmistakably Linux has already contradicted itself
before a single HTTP byte left the host — a cross-layer inconsistency of exactly
the kind FP-Inconsistent (arXiv:2406.07647) exploits.

This module splits the problem honestly in two (constitution P1):

  **Specification** — :class:`TcpStackProfile` fully describes a stack signature,
  including the fields a normal socket cannot change. :meth:`syn_signature` and
  :meth:`option_kinds` are complete enough for an operator with a raw-socket or
  BPF path to emit the real thing.

  **Application** — :func:`apply_to_socket` sets what ``setsockopt`` genuinely
  controls (``IP_TTL``, ``TCP_MAXSEG``, ``TCP_NODELAY``, send/receive buffers)
  and reports everything else in :attr:`EmulationReport.unsupported`. It never
  claims to have emulated a kernel-owned field.

Kernel-owned and therefore reported, never faked: window scaling factor, TCP
option *ordering*, timestamps, DF bit, ISN pattern, and initial window sizing.
Those need a raw socket (and usually root); emulating them from a userspace
socket is not possible, and pretending otherwise would be a lie in the metadata.
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from omk_crawl.fingerprint import FingerprintProfile

__all__ = [
    "EmulationReport",
    "STACKS",
    "TcpStackProfile",
    "apply_to_socket",
    "audit_tcp",
    "signature_accounting",
    "stack_for",
]

# TCP option kinds (IANA). The *order* is the fingerprint, not the set.
OPT_END = 0
OPT_NOP = 1
OPT_MSS = 2
OPT_WINDOW_SCALE = 3
OPT_SACK_PERMITTED = 4
OPT_TIMESTAMPS = 8

_OPTION_NAMES: dict[int, str] = {
    OPT_END: "eol",
    OPT_NOP: "nop",
    OPT_MSS: "mss",
    OPT_WINDOW_SCALE: "ws",
    OPT_SACK_PERMITTED: "sackOK",
    OPT_TIMESTAMPS: "ts",
}


@dataclass(frozen=True, slots=True)
class TcpStackProfile:
    """One OS's TCP/IP SYN signature.

    Attributes:
        name: Identifier ("windows-11").
        os_family: The OS this stack belongs to ("Windows", "macOS", "Linux",
            "Android", "iOS"). This is what must agree with the UA.
        ttl: Initial TTL (64 for the BSD/Linux family, 128 for Windows).
        window: Initial advertised receive window.
        mss: Maximum segment size.
        window_scaling: Window-scale shift requested in the SYN.
        sack_ok: Whether SACK is permitted.
        timestamps: Whether RFC 1323 timestamps are requested.
        df: Whether the DF bit is set on the IP header.
        options: TCP option kinds in **wire order**. Order is the tell.
    """

    name: str
    os_family: str
    ttl: int
    window: int
    mss: int = 1460
    window_scaling: int = 7
    sack_ok: bool = True
    timestamps: bool = True
    df: bool = True
    options: tuple[int, ...] = (OPT_MSS, OPT_SACK_PERMITTED, OPT_TIMESTAMPS, OPT_NOP,
                               OPT_WINDOW_SCALE)

    def option_kinds(self) -> tuple[int, ...]:
        """TCP option kinds in wire order."""
        return self.options

    def option_names(self) -> list[str]:
        """Human-readable option order for audit output."""
        return [_OPTION_NAMES.get(k, f"kind{k}") for k in self.options]

    def syn_signature(self) -> dict[str, Any]:
        """Complete, JSON-safe signature — the spec an operator can wire up.

        Includes the fields :func:`apply_to_socket` cannot set, so the value is
        useful as a target spec rather than a record of what was achieved.
        """
        return {
            "name": self.name,
            "os_family": self.os_family,
            "ttl": self.ttl,
            "window": self.window,
            "mss": self.mss,
            "window_scaling": self.window_scaling,
            "sack_ok": self.sack_ok,
            "timestamps": self.timestamps,
            "df": self.df,
            "options": list(self.options),
            "option_names": self.option_names(),
        }


# ── Documented stack signatures ────────────────────────────────────────────
# Values are the widely-published typical SYN parameters for each OS family;
# the option *ordering* is what passive fingerprinters key on.

STACKS: tuple[TcpStackProfile, ...] = (
    TcpStackProfile(
        name="windows-11",
        os_family="Windows",
        ttl=128,
        window=64240,
        mss=1460,
        window_scaling=8,
        # Windows places NOPs around the window scale — the classic tell.
        options=(OPT_MSS, OPT_NOP, OPT_WINDOW_SCALE, OPT_NOP, OPT_NOP, OPT_SACK_PERMITTED),
    ),
    TcpStackProfile(
        name="windows-10",
        os_family="Windows",
        ttl=128,
        window=64240,
        mss=1460,
        window_scaling=8,
        options=(OPT_MSS, OPT_NOP, OPT_WINDOW_SCALE, OPT_NOP, OPT_NOP, OPT_SACK_PERMITTED),
    ),
    TcpStackProfile(
        name="macos-14",
        os_family="macOS",
        ttl=64,
        window=65535,
        mss=1460,
        window_scaling=6,
        options=(OPT_MSS, OPT_NOP, OPT_WINDOW_SCALE, OPT_NOP, OPT_NOP, OPT_TIMESTAMPS,
                 OPT_SACK_PERMITTED),
    ),
    TcpStackProfile(
        name="linux-6",
        os_family="Linux",
        ttl=64,
        window=29200,
        mss=1460,
        window_scaling=7,
        options=(OPT_MSS, OPT_SACK_PERMITTED, OPT_TIMESTAMPS, OPT_NOP, OPT_WINDOW_SCALE),
    ),
    TcpStackProfile(
        name="android-14",
        os_family="Android",
        ttl=64,
        window=65535,
        mss=1460,
        window_scaling=7,
        options=(OPT_MSS, OPT_SACK_PERMITTED, OPT_TIMESTAMPS, OPT_NOP, OPT_WINDOW_SCALE),
    ),
    TcpStackProfile(
        name="ios-17",
        os_family="iOS",
        ttl=64,
        window=65535,
        mss=1460,
        window_scaling=6,
        options=(OPT_MSS, OPT_NOP, OPT_WINDOW_SCALE, OPT_NOP, OPT_NOP, OPT_TIMESTAMPS,
                 OPT_SACK_PERMITTED),
    ),
)

_STACKS_BY_NAME: dict[str, TcpStackProfile] = {s.name: s for s in STACKS}
_STACKS_BY_OS: dict[str, TcpStackProfile] = {}
for _stack in STACKS:
    # First entry per OS family wins — the tuple is ordered newest-first.
    _STACKS_BY_OS.setdefault(_stack.os_family, _stack)
_DEFAULT_STACK = _STACKS_BY_OS["Linux"]


def stack_for(profile: FingerprintProfile) -> TcpStackProfile:
    """The stack whose OS matches the profile's OS claim.

    This is the mechanism that keeps the TCP layer from contradicting the UA:
    the stack is not chosen independently, it is *derived* from
    :attr:`FingerprintProfile.platform_os`. Unknown OS strings fall back to the
    Linux stack deterministically rather than raising, so planning never fails
    on an exotic UA.
    """
    return _STACKS_BY_OS.get(profile.platform_os, _DEFAULT_STACK)


# ─ Application ────────────────────────────────────────────────────────────

# Fields no userspace socket can control on any supported platform. ``sack_ok``
# belongs here rather than in the applied set: SACK is toggled system-wide via
# ``net.ipv4.tcp_sack``, not per socket, so a socket cannot advertise it one way
# or the other.
_KERNEL_OWNED: tuple[str, ...] = (
    "window_scaling",
    "options",
    "sack_ok",
    "timestamps",
    "df",
    "isn",
    "initial_window",
)

# Signature fields applied directly by name.
_APPLIED: tuple[str, ...] = ("ttl", "mss")

# Signature fields reached only through an option with a different name. The
# advertised window cannot be set; ``SO_RCVBUF`` is what bounds it, and the
# report says so instead of implying a direct set.
_PROXIED: dict[str, str] = {"window": "rcvbuf"}

# Signature fields that are descriptors, not tunables.
_METADATA_FIELDS: tuple[str, ...] = ("name", "os_family", "option_names")


def signature_accounting() -> dict[str, str]:
    """Bucket for every key of :meth:`TcpStackProfile.syn_signature`.

    Declared once so the completeness test can assert that no signature field is
    silently ignored. Adding a field to the signature without deciding how it is
    handled fails the suite rather than dropping it on the floor — the rule P2
    asks for, made mechanical.
    """
    buckets: dict[str, str] = {name: "metadata" for name in _METADATA_FIELDS}
    buckets.update({name: "applied" for name in _APPLIED})
    buckets.update({name: f"proxied:{proxy}" for name, proxy in _PROXIED.items()})
    for name in _KERNEL_OWNED:
        buckets.setdefault(name, "unsupported")
    return buckets


@dataclass(slots=True)
class EmulationReport:
    """What was actually applied, and what could not be.

    ``applied`` maps a stack field to the value the kernel accepted (which may
    differ from the requested one — buffers get clamped, so the *observed*
    value is recorded, not the requested one).
    """

    stack: str
    applied: dict[str, Any] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    proxied: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when at least one field was applied and none errored outright."""
        return bool(self.applied) and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "stack": self.stack,
            "applied": dict(self.applied),
            "proxied": dict(self.proxied),
            "unsupported": list(self.unsupported),
            "errors": dict(self.errors),
        }


def apply_to_socket(sock: socket.socket, stack: TcpStackProfile) -> EmulationReport:
    """Apply every controllable stack field to ``sock``; report the rest.

    Never raises: each field is attempted independently and a failure is
    recorded in :attr:`EmulationReport.errors` while the others still apply.
    This keeps the caller's happy path intact without hiding the loss — the
    difference matters on platforms where an option is unavailable, and for
    socket types that reject TCP-level options.

    The socket should be a TCP socket (``SOCK_STREAM``); ``TCP_MAXSEG`` is
    meaningless otherwise. Connect-time options (buffers, MSS) are most
    effective when set before ``connect()``.
    """
    report = EmulationReport(
        stack=stack.name,
        unsupported=list(_KERNEL_OWNED),
        proxied=dict(_PROXIED),
    )

    def _attempt(field_name: str, level: int, opt: int, value: int) -> None:
        try:
            sock.setsockopt(level, opt, value)
            report.applied[field_name] = sock.getsockopt(level, opt)
        except (OSError, ValueError, OverflowError, AttributeError) as exc:
            report.errors[field_name] = f"{type(exc).__name__}: {exc}"

    if hasattr(socket, "IP_TTL"):
        _attempt("ttl", socket.IPPROTO_IP, socket.IP_TTL, stack.ttl)
    if hasattr(socket, "TCP_MAXSEG"):
        _attempt("mss", socket.IPPROTO_TCP, socket.TCP_MAXSEG, stack.mss)
    if hasattr(socket, "TCP_NODELAY"):
        _attempt("nodelay", socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    # Receive buffer bounds the advertised window; asking for a size near the
    # target window is the closest a userspace socket gets to matching it.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, max(stack.window, 4096))
        report.applied["rcvbuf"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    except (OSError, ValueError, OverflowError, AttributeError) as exc:
        report.errors["rcvbuf"] = f"{type(exc).__name__}: {exc}"

    return report


# ── Audit ──────────────────────────────────────────────────────────────────


def audit_tcp(
    observed: Mapping[str, Any],
    stack: TcpStackProfile,
    profile: FingerprintProfile | None = None,
) -> list[str]:
    """Compare an observed SYN signature (or socket state) against a stack.

    Only keys present in ``observed`` are checked, so a partial capture still
    yields a useful report. When ``profile`` is supplied, the stack's OS is
    also checked against the UA's OS claim — the cross-layer rule that matters
    most, since a self-consistent stack that disagrees with the UA is still a
    contradiction.
    """
    issues: list[str] = []

    if profile is not None and stack.os_family.lower() != profile.platform_os.lower():
        issues.append(
            f"TCP stack {stack.name} is {stack.os_family} but UA claims {profile.platform_os}"
        )

    scalar_fields = (
        ("ttl", stack.ttl),
        ("window", stack.window),
        ("mss", stack.mss),
        ("window_scaling", stack.window_scaling),
        ("sack_ok", stack.sack_ok),
        ("timestamps", stack.timestamps),
        ("df", stack.df),
    )
    for key, expected in scalar_fields:
        if key not in observed:
            continue
        actual = observed[key]
        if actual != expected:
            issues.append(f"observed {key}={actual!r} != stack {stack.name} {key}={expected!r}")

    if "options" in observed:
        actual_options = tuple(observed["options"] or ())
        if actual_options != stack.options:
            issues.append(
                f"observed TCP option order {list(actual_options)} != "
                f"{list(stack.options)} ({', '.join(stack.option_names())})"
            )

    return issues
