"""TLS / JA3-JA4 normalization — the bottom layer of the identity story.

The handshake is the first thing a server sees and the hardest to fake: TLS
bad-bot detection (arXiv:2602.09606) separates stock HTTP clients from real
browsers *before* a single header arrives. v2.12 pinned the TLS target through
``curl_cffi``'s ``impersonate`` (a real wire stack); this module adds what that
was missing — a *model* of the ClientHello that can be

  1. **audited** against a :class:`~omk_crawl.fingerprint.FingerprintProfile`
     (does the TLS family agree with the UA family?), and
  2. **normalized** so GREASE and ordering churn do not read as fingerprint
     drift over time (the temporal axis FP-Inconsistent measures).

Honesty boundary (constitution P1): the values here are *derived models*, not
captured wire traffic. They exist so profiles can be checked offline and so
drift is detectable. The wire handshake is produced by ``curl_cffi`` against a
real browser build; this module never claims to have emitted a byte.

Family invariants encoded below are the well-established, observable ones:

  ==========  =======  ======  ===========
  family      GREASE   ALPN    TLS 1.3
  ==========  =======  ======  ===========
  chrome      yes      h2      yes
  firefox     no       h2      yes
  safari      no       h2      yes
  ==========  =======  ======  ===========

A Chrome UA on a handshake without GREASE (or a Firefox UA on one with it) is
a cross-layer contradiction — exactly what the audit reports.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from omk_crawl.fingerprint import FingerprintProfile

__all__ = [
    "CIPHER_NAMES",
    "GREASE_VALUES",
    "TlsClientHello",
    "audit_tls",
    "drift_report",
    "hello_for",
    "is_grease",
    "ja3",
    "ja3_string",
    "normalize_grease",
]

# RFC 8701 GREASE — reserved values browsers scatter through the hello to keep
# middleboxes honest. Their *presence* is a family tell; their *values* rotate
# per connection, so they must be stripped before any stable hash is computed.
GREASE_VALUES: frozenset[int] = frozenset(
    {0x0A0A, 0x1A1A, 0x2A2A, 0x3A3A, 0x4A4A, 0x5A5A, 0x6A6A, 0x7A7A,
     0x8A8A, 0x9A9A, 0xAAAA, 0xBABA, 0xCACA, 0xDADA, 0xEAEA, 0xFAFA}
)

# Enough of the IANA registry to make audits readable; unknown ids render as
# their hex form rather than being dropped.
CIPHER_NAMES: dict[int, str] = {
    0x002F: "TLS_RSA_WITH_AES_128_CBC_SHA",
    0x0035: "TLS_RSA_WITH_AES_256_CBC_SHA",
    0x009C: "TLS_RSA_WITH_AES_128_GCM_SHA256",
    0x009D: "TLS_RSA_WITH_AES_256_GCM_SHA384",
    0xC013: "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA",
    0xC014: "TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA",
    0xC02B: "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256",
    0xC02C: "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384",
    0xC02F: "TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256",
    0xC030: "TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384",
    0xCCA8: "TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256",
    0xCCA9: "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256",
    0x1301: "TLS_AES_128_GCM_SHA256",
    0x1302: "TLS_AES_256_GCM_SHA384",
    0x1303: "TLS_CHACHA20_POLY1305_SHA256",
    0x1304: "TLS_AES_128_CCM_SHA256",
    0x1305: "TLS_AES_128_CCM_8_SHA256",
}

# Extension ids referenced by name in audits.
_EXT_NAMES: dict[int, str] = {
    0x0000: "server_name",
    0x0005: "status_request",
    0x000A: "supported_groups",
    0x000B: "ec_point_formats",
    0x000D: "signature_algorithms",
    0x0010: "alpn",
    0x0012: "signed_certificate_timestamp",
    0x0015: "padding",
    0x0017: "extended_master_secret",
    0x001B: "certificate_authorities",
    0x001C: "record_size_limit",
    0x0022: "delegated_credentials",
    0x0023: "session_ticket",
    0x002B: "supported_versions",
    0x002D: "psk_key_exchange_modes",
    0x0033: "key_share",
    0x4469: "application_settings",
    0xFE0D: "encrypted_client_hello",
    0xFF01: "renegotiation_info",
}

_TLS13_CIPHERS: frozenset[int] = frozenset({0x1301, 0x1302, 0x1303, 0x1304, 0x1305})


def is_grease(value: int) -> bool:
    """True when ``value`` is a reserved GREASE codepoint (RFC 8701)."""
    return value in GREASE_VALUES


def normalize_grease(values: tuple[int, ...]) -> tuple[int, ...]:
    """Drop GREASE in order-preserving fashion. Idempotent.

    GREASE values are re-drawn per connection by design, so any hash that keeps
    them is unstable between two identical browsers. Stripping is therefore the
    precondition for *stable* fingerprints, not an optimization.
    """
    return tuple(v for v in values if v not in GREASE_VALUES)


@dataclass(frozen=True, slots=True)
class TlsClientHello:
    """A modeled ClientHello — enough to fingerprint, not a wire encoder.

    Attributes:
        record_version: Legacy record-layer version (0x0303 for TLS 1.2+).
        cipher_suites: Offered ciphers, in wire order, GREASE included.
        extensions: Offered extension ids, in wire order, GREASE included.
        elliptic_curves: ``supported_groups`` values, in wire order.
        ec_point_formats: ``ec_point_formats`` values.
        alpn: ALPN protocol list (``("h2", "http/1.1")``).
        tls13: Whether TLS 1.3 was offered (via ``supported_versions``).
    """

    record_version: int = 0x0303
    cipher_suites: tuple[int, ...] = ()
    extensions: tuple[int, ...] = ()
    elliptic_curves: tuple[int, ...] = ()
    ec_point_formats: tuple[int, ...] = (0,)
    alpn: tuple[str, ...] = ("h2", "http/1.1")
    tls13: bool = True

    # ─ Normalization ───────────────────────────────────────────────────

    def normalized(self) -> TlsClientHello:
        """GREASE-free copy of this hello."""
        return replace(
            self,
            cipher_suites=normalize_grease(self.cipher_suites),
            extensions=normalize_grease(self.extensions),
            elliptic_curves=normalize_grease(self.elliptic_curves),
        )

    @property
    def has_grease(self) -> bool:
        """Whether any GREASE codepoint appears anywhere in the hello."""
        return any(
            is_grease(v)
            for v in (*self.cipher_suites, *self.extensions, *self.elliptic_curves)
        )

    @property
    def offers_tls13(self) -> bool:
        """TLS 1.3 offered either by version flag or by a 1.3-only cipher."""
        return self.tls13 or bool(_TLS13_CIPHERS & set(self.cipher_suites))

    # ── Fingerprints ────────────────────────────────────────────────────

    def ja3_string(self) -> str:
        """Canonical JA3 string over the **GREASE-free** hello.

        ``SSLVersion,Ciphers,Extensions,EllipticCurves,ECPointFormats`` — each
        list ``-`` joined, decimal. GREASE is removed first so the value is
        stable across connections from the same build.

        The extension list in this model is stored sorted (Chromium randomises
        the wire order), so the digest identifies the model rather than
        reproducing an observed handshake.
        """
        norm = self.normalized()
        return ",".join(
            (
                str(norm.record_version),
                "-".join(str(c) for c in norm.cipher_suites),
                "-".join(str(e) for e in norm.extensions),
                "-".join(str(g) for g in norm.elliptic_curves),
                "-".join(str(p) for p in norm.ec_point_formats),
            )
        )

    def ja3(self) -> str:
        """MD5 hex of :meth:`ja3_string` — the conventional JA3 digest."""
        return hashlib.md5(self.ja3_string().encode()).hexdigest()

    def ja4_model(self) -> str:
        """JA4-*style* identifier ``a_b_c`` for this modeled hello.

        ``a`` follows the published JA4 layout (transport, version, SNI,
        2-digit GREASE-free cipher/extension counts, ALPN marker). ``b``/``c``
        are the documented truncated-sha256 scheme over the sorted GREASE-free
        cipher and extension lists.

        Deviation, stated explicitly: the real JA4 ``c`` also folds in the
        signature-algorithms list, which this model does not carry. The value
        is therefore a **stable model identifier** for drift detection, not a
        drop-in JA4 replacement.
        """
        norm = self.normalized()
        version = "13" if self.offers_tls13 else "12"
        sni = "d" if 0x0000 in norm.extensions else "i"
        alpn = ""
        if norm.alpn:
            first = norm.alpn[0]
            alpn = (first[0] + first[-1]) if first else ""
        part_a = (
            f"t{version}{sni}{len(norm.cipher_suites):02d}{len(norm.extensions):02d}{alpn}"
        )

        def _digest(values: tuple[int, ...]) -> str:
            joined = ",".join(f"{v:04x}" for v in sorted(values))
            return hashlib.sha256(joined.encode()).hexdigest()[:12]

        return f"{part_a}_{_digest(norm.cipher_suites)}_{_digest(norm.extensions)}"

    def signature(self) -> dict[str, Any]:
        """JSON-safe summary for result metadata and drift storage."""
        norm = self.normalized()
        return {
            "ja3": self.ja3(),
            "ja4_model": self.ja4_model(),
            "grease": self.has_grease,
            "tls13": self.offers_tls13,
            "ciphers": len(norm.cipher_suites),
            "extensions": len(norm.extensions),
            "alpn": list(self.alpn),
        }

    def cipher_names(self) -> list[str]:
        """Human-readable cipher list (unknown ids as ``0xNNNN``)."""
        return [
            CIPHER_NAMES.get(c, f"0x{c:04x}") for c in normalize_grease(self.cipher_suites)
        ]

    def extension_names(self) -> list[str]:
        """Human-readable extension list (unknown ids as ``0xNNNN``)."""
        return [
            _EXT_NAMES.get(e, f"0x{e:04x}") for e in normalize_grease(self.extensions)
        ]


# ── Family models ──────────────────────────────────────────────────────────
# Ordered shapes per browser family. Chrome scatters GREASE through every list;
# Firefox and Safari do not, which is an observable, checkable family invariant.
#
# The cipher lists below were reconciled against real handshakes from the exact
# curl_cffi impersonation targets (tls.browserleaks.com/json) rather than
# transcribed from documentation. Chrome's list matched the wire entry for
# entry; Firefox and Safari were corrected after the comparison showed the
# earlier model missing suites and carrying the wrong CBC pair.
# tests/test_evasion_live.py re-checks this against the wire.
#
# Extension lists are stored **sorted**, deliberately. Chromium randomises the
# wire order of its extensions on every connection precisely to defeat
# order-sensitive JA3-style fingerprints, so storing one observed order would
# imply a stability that does not exist. Firefox and Safari send a stable
# order, but normalising every family the same way keeps ja4_model() (which
# hashes sorted lists) meaningful and makes drift_report immune to ordering
# churn. Consequence, stated plainly: the JA3 this module produces identifies
# the model; it is not a value you would observe on the wire.

_CHROME_GREASE = 0x0A0A

_CHROME_CIPHERS: tuple[int, ...] = (
    _CHROME_GREASE,
    0x1301, 0x1302, 0x1303,
    0xC02B, 0xC02F, 0xC02C, 0xC030,
    0xCCA9, 0xCCA8,
    0xC013, 0xC014,
    0x009C, 0x009D,
    0x002F, 0x0035,
)

_CHROME_EXTENSIONS: tuple[int, ...] = (
    _CHROME_GREASE,
    0x0000, 0x0005, 0x000A, 0x000B, 0x000D, 0x0010, 0x0012, 0x0017,
    0x001B, 0x0023, 0x002B, 0x002D, 0x0033, 0x4469, 0xFE0D, 0xFF01,
)

_FIREFOX_CIPHERS: tuple[int, ...] = (
    0x1301, 0x1303, 0x1302,
    0xC02B, 0xC02F, 0xCCA9, 0xCCA8, 0xC02C, 0xC030,
    0xC00A, 0xC009,
    0xC013, 0xC014,
    0x009C, 0x009D,
    0x002F, 0x0035,
)

_FIREFOX_EXTENSIONS: tuple[int, ...] = (
    0x0000, 0x0005, 0x000A, 0x000B, 0x000D, 0x0010, 0x0017, 0x001B,
    0x001C, 0x0022, 0x0023, 0x002B, 0x002D, 0x0033, 0xFE0D, 0xFF01,
)

_SAFARI_CIPHERS: tuple[int, ...] = (
    0x1301, 0x1302, 0x1303,
    0xC02C, 0xC02B, 0xCCA9, 0xC030, 0xC02F, 0xCCA8,
    0xC00A, 0xC009,
    0xC014, 0xC013,
    0x009D, 0x009C,
    0x0035, 0x002F,
    0xC008, 0xC012,
    0x000A,
)

_SAFARI_EXTENSIONS: tuple[int, ...] = (
    0x0000, 0x0005, 0x000A, 0x000B, 0x000D, 0x0010, 0x0012, 0x0015,
    0x0017, 0x001B, 0x002B, 0x002D, 0x0033, 0xFF01,
)

# Modern curves first (X25519), then NIST, then FFDHE where the family sends it.
_CURVES_CHROMIUM: tuple[int, ...] = (0x001D, 0x0017, 0x0018, 0x0019, 0x0100)
_CURVES_FIREFOX: tuple[int, ...] = (0x001D, 0x0017, 0x0018, 0x0019, 0x0100)
_CURVES_SAFARI: tuple[int, ...] = (0x001D, 0x0017, 0x0018)

_FAMILY_MODELS: dict[str, TlsClientHello] = {
    "chrome": TlsClientHello(
        cipher_suites=_CHROME_CIPHERS,
        extensions=_CHROME_EXTENSIONS,
        elliptic_curves=(_CHROME_GREASE, *_CURVES_CHROMIUM),
    ),
    "edge": TlsClientHello(
        cipher_suites=_CHROME_CIPHERS,
        extensions=_CHROME_EXTENSIONS,
        elliptic_curves=(_CHROME_GREASE, *_CURVES_CHROMIUM),
    ),
    "firefox": TlsClientHello(
        cipher_suites=_FIREFOX_CIPHERS,
        extensions=_FIREFOX_EXTENSIONS,
        elliptic_curves=_CURVES_FIREFOX,
    ),
    "safari": TlsClientHello(
        cipher_suites=_SAFARI_CIPHERS,
        extensions=_SAFARI_EXTENSIONS,
        elliptic_curves=_CURVES_SAFARI,
    ),
}


def hello_for(profile: FingerprintProfile) -> TlsClientHello:
    """Deterministic ClientHello model for a profile's browser family.

    Keyed on :attr:`FingerprintProfile.family`, which is itself derived from the
    UA — so the TLS model and the User-Agent cannot disagree by construction
    (constitution P7).
    """
    return _FAMILY_MODELS.get(profile.family, _FAMILY_MODELS["chrome"])


def audit_tls(hello: TlsClientHello, profile: FingerprintProfile) -> list[str]:
    """Cross-layer TLS vs UA audit. Empty list means the two agree.

    Rules encoded (each a documented family invariant):
      1. GREASE presence must match the family.
      2. ALPN must be present — every modern browser offers h2.
      3. TLS 1.3 must be offered by any current browser family.
      4. A Chrome-family UA must offer at least one 1.3-only cipher.
    """
    issues: list[str] = []
    family = profile.family
    grease_expected = family in ("chrome", "edge")

    if hello.has_grease != grease_expected:
        if grease_expected:
            issues.append(f"UA claims {family} but hello carries no GREASE (non-Chromium stack?)")
        else:
            issues.append(f"UA claims {family} but hello carries GREASE (Chromium stack?)")

    if not hello.alpn:
        issues.append("hello offers no ALPN — real browsers negotiate h2")

    if not hello.offers_tls13:
        issues.append("hello does not offer TLS 1.3 — no current browser omits it")

    if grease_expected and not (_TLS13_CIPHERS & set(hello.cipher_suites)):
        issues.append("Chrome-family UA without a TLS 1.3 cipher suite in the offer")

    return issues


def drift_report(previous: TlsClientHello, current: TlsClientHello) -> dict[str, Any]:
    """Compare two hellos from the *same* site over time (temporal axis).

    GREASE is normalized away before comparison, so a report of ``stable=True``
    means the client is presenting one identity rather than rotating through
    several — the second axis FP-Inconsistent measures.
    """
    prev, cur = previous.normalized(), current.normalized()
    added_ciphers = sorted(set(cur.cipher_suites) - set(prev.cipher_suites))
    dropped_ciphers = sorted(set(prev.cipher_suites) - set(cur.cipher_suites))
    added_ext = sorted(set(cur.extensions) - set(prev.extensions))
    dropped_ext = sorted(set(prev.extensions) - set(cur.extensions))
    stable = not (added_ciphers or dropped_ciphers or added_ext or dropped_ext)
    return {
        "stable": stable,
        "ja3_changed": prev.ja3() != cur.ja3(),
        "previous_ja3": prev.ja3(),
        "current_ja3": cur.ja3(),
        "added_ciphers": added_ciphers,
        "dropped_ciphers": dropped_ciphers,
        "added_extensions": added_ext,
        "dropped_extensions": dropped_ext,
    }


# ── Module-level convenience (kept thin; the class owns the logic) ─────────


def ja3_string(hello: TlsClientHello) -> str:
    """Shorthand for :meth:`TlsClientHello.ja3_string`."""
    return hello.ja3_string()


def ja3(hello: TlsClientHello) -> str:
    """Shorthand for :meth:`TlsClientHello.ja3`."""
    return hello.ja3()
