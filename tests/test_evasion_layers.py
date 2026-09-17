"""Tests for the v2.14 deep evasion layers (offline, deterministic).

Covers all six tracks end to end:

  tcp.py            stack profiles, real setsockopt application, audits
  tls.py            GREASE normalization, JA3/JA4 models, family audits
  cdp.py            tell registry, patch composition, leak auditing
  browser_props.py  property specs, spoof scripts, cross-layer audits
  behavior.py       typing dynamics and session rhythm (extensions)
  captcha.py        challenge classification and the solver policy
  evasion.py        the six-layer arbiter and its coherence report
  verify.py         the offline mock detector

Two regression tests exist because a real Chromium found real bugs during
development, not because they were imagined: a tell without a patch raised
KeyError, and a fragment emitted once per tell redeclared its `const`s and
aborted the whole composed script with a SyntaxError.
"""

from __future__ import annotations

import json
import socket
import threading

import pytest

from omk_crawl.behavior import BehaviorClock, Keystroke, SessionPhase
from omk_crawl.browser_props import (
    BOOTSTRAP,
    audit_props,
    property_spec,
    spoof_script,
    webgl_identity,
)
from omk_crawl.captcha import (
    DEFAULT_BACKENDS,
    CaptchaKind,
    HttpSolver,
    MockSolver,
    NullSolver,
    classify_captcha,
    resolve_challenge,
)
from omk_crawl.cdp import (
    _DELEGATED,
    _PROFILE_FIXED,
    _TELL_FRAGMENT,
    CDP_TELLS,
    DRIVER_FIXED,
    audit_cdp,
    full_script,
    patch_body,
    patch_js,
    patch_plan,
)
from omk_crawl.cdp import (
    probe_script as cdp_probe_script,
)
from omk_crawl.evasion import plan_for
from omk_crawl.fingerprint import PROFILES, profile_for
from omk_crawl.tcp import (
    STACKS,
    apply_to_socket,
    audit_tcp,
    signature_accounting,
    stack_for,
)
from omk_crawl.tls import (
    GREASE_VALUES,
    TlsClientHello,
    audit_tls,
    drift_report,
    hello_for,
    is_grease,
    normalize_grease,
)
from omk_crawl.verify import (
    evasion_surface,
    naive_stealth_surface,
    score,
    stock_surface,
)

# ── TCP stack layer (AC-1, AC-2) ─────────────────────────────────────────


class TestTcpStacks:
    def test_stack_matches_ua_os_for_every_profile(self):
        for profile in PROFILES:
            stack = stack_for(profile)
            assert stack.os_family.lower() == profile.platform_os.lower(), profile.name

    def test_stack_names_are_unique(self):
        names = [stack.name for stack in STACKS]
        assert len(names) == len(set(names))

    def test_windows_and_unix_option_orders_differ(self):
        """Option ordering is the passive-fingerprint tell, so it must differ."""
        windows = next(s for s in STACKS if s.name == "windows-11")
        linux = next(s for s in STACKS if s.name == "linux-6")
        assert windows.options != linux.options
        assert windows.ttl != linux.ttl
        assert windows.window_scaling != linux.window_scaling

    def test_unknown_os_falls_back_deterministically(self):
        class _Odd:
            platform_os = "Plan9"
            name = "odd"

        assert stack_for(_Odd()).name == stack_for(_Odd()).name  # type: ignore[arg-type]

    def test_audit_catches_ttl_mismatch(self):
        stack = next(s for s in STACKS if s.name == "windows-11")
        issues = audit_tcp({"ttl": 64}, stack)
        assert any("ttl" in issue for issue in issues)

    def test_audit_catches_option_order_mismatch(self):
        stack = next(s for s in STACKS if s.name == "windows-11")
        issues = audit_tcp({"options": (2, 4, 8)}, stack)
        assert any("option order" in issue for issue in issues)

    def test_audit_catches_os_disagreement_with_ua(self):
        windows_stack = next(s for s in STACKS if s.name == "windows-11")
        linux_profile = next(p for p in PROFILES if p.platform_os == "Linux")
        issues = audit_tcp({}, windows_stack, linux_profile)
        assert any("UA claims" in issue for issue in issues)

    def test_self_signature_is_clean(self):
        for stack in STACKS:
            assert audit_tcp(stack.syn_signature(), stack) == []

    def test_apply_to_socket_changes_ttl_for_real(self):
        """The claim 'we set TTL' is only worth anything if the kernel agrees."""
        stack = next(s for s in STACKS if s.name == "windows-11")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            report = apply_to_socket(sock, stack)
            assert report.errors == {}
            assert report.applied["ttl"] == stack.ttl
            assert sock.getsockopt(socket.IPPROTO_IP, socket.IP_TTL) == stack.ttl

    def test_apply_reports_kernel_owned_fields_as_unsupported(self):
        """Emulation must not claim what a userspace socket cannot do (P1)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            report = apply_to_socket(sock, STACKS[0])
        assert "window_scaling" in report.unsupported
        assert "options" in report.unsupported
        assert "timestamps" in report.unsupported
        assert report.ok

    def test_emulation_report_serializes(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            data = apply_to_socket(sock, STACKS[0]).to_dict()
        assert json.dumps(data)  # JSON-safe, no socket objects leaked
        assert data["stack"] == STACKS[0].name


# ── TLS / JA3 layer (AC-3) ───────────────────────────────────────────────


class TestTlsLayer:
    def test_grease_detection(self):
        assert is_grease(0x0A0A)
        assert is_grease(0xFAFA)
        assert not is_grease(0x1301)
        assert len(GREASE_VALUES) == 16

    def test_normalization_is_idempotent(self):
        values = (0x0A0A, 0x1301, 0x1A1A, 0xC02F)
        once = normalize_grease(values)
        assert once == (0x1301, 0xC02F)
        assert normalize_grease(once) == once

    def test_ja3_is_stable_across_grease_values(self):
        """GREASE rotates per connection; a stable hash must ignore it."""
        a = TlsClientHello(cipher_suites=(0x0A0A, 0x1301), extensions=(0x1A1A, 0x0000))
        b = TlsClientHello(cipher_suites=(0x3A3A, 0x1301), extensions=(0x8A8A, 0x0000))
        assert a.ja3() == b.ja3()
        assert a.ja3_string() == b.ja3_string()

    def test_same_profile_gives_same_hello_and_ja3(self):
        profile = PROFILES[0]
        assert hello_for(profile) == hello_for(profile)
        assert hello_for(profile).ja3() == hello_for(profile).ja3()

    def test_families_produce_distinct_ja3(self):
        by_family: dict[str, str] = {}
        for profile in PROFILES:
            by_family.setdefault(profile.family, hello_for(profile).ja3())
        assert len(by_family) == 3
        assert len(set(by_family.values())) == 3

    def test_grease_follows_family_not_ua_string(self):
        hello = hello_for(PROFILES[0])
        assert hello.has_grease == (PROFILES[0].family in ("chrome", "edge"))

    def test_audit_catches_family_mismatch(self):
        firefox_profile = profile_for("https://x.test", salt="ff")
        while firefox_profile.family != "firefox":
            firefox_profile = profile_for("https://x.test", salt=f"{firefox_profile.name}x")
        chrome_hello = hello_for(PROFILES[0])
        issues = audit_tls(chrome_hello, firefox_profile)
        assert any("GREASE" in issue for issue in issues)

    def test_audit_catches_missing_alpn(self):
        bare = TlsClientHello(cipher_suites=(0x1301,), extensions=(0x0000,), alpn=())
        issues = audit_tls(bare, PROFILES[0])
        assert any("ALPN" in issue for issue in issues)

    def test_audit_catches_missing_tls13(self):
        old = TlsClientHello(cipher_suites=(0xC02F,), extensions=(0x0000,), tls13=False)
        issues = audit_tls(old, PROFILES[0])
        assert any("TLS 1.3" in issue for issue in issues)

    def test_builtin_hellos_are_coherent_with_their_profiles(self):
        for profile in PROFILES:
            assert audit_tls(hello_for(profile), profile) == [], profile.name

    def test_drift_report_detects_rotation(self):
        a = TlsClientHello(cipher_suites=(0x1301, 0x1302), extensions=(0x0000,))
        b = TlsClientHello(cipher_suites=(0x1301,), extensions=(0x0000, 0x0010))
        report = drift_report(a, b)
        assert not report["stable"]
        assert report["ja3_changed"]
        assert report["dropped_ciphers"] == [0x1302]
        assert report["added_extensions"] == [0x0010]

    def test_drift_report_ignores_grease_only_churn(self):
        a = TlsClientHello(cipher_suites=(0x0A0A, 0x1301), extensions=(0x1A1A, 0x0000))
        b = TlsClientHello(cipher_suites=(0x2A2A, 0x1301), extensions=(0x3A3A, 0x0000))
        assert drift_report(a, b)["stable"]

    def test_ja4_model_shape(self):
        ja4 = hello_for(PROFILES[0]).ja4_model()
        head, cipher_hash, ext_hash = ja4.split("_")
        assert head.startswith("t13")
        assert len(cipher_hash) == 12 and len(ext_hash) == 12

    def test_signature_is_json_safe(self):
        assert json.dumps(hello_for(PROFILES[0]).signature())


# ── CDP layer (AC-4, AC-5) ───────────────────────────────────────────────


class TestCdpLayer:
    def test_every_patchable_tell_has_a_fragment_or_a_delegate(self):
        """Regression: a patchable tell with no fragment raised KeyError."""
        for tell in CDP_TELLS:
            if not tell.patchable:
                continue
            assert tell.key in _TELL_FRAGMENT or tell.key in _DELEGATED, tell.key

    def test_no_fragment_is_emitted_twice(self):
        """Regression: a duplicated fragment redeclares its consts and kills
        the whole composed script with a SyntaxError."""
        plan = patch_plan(PROFILES[0])
        bodies = [patch.js for patch in plan.patches if patch.source != "bootstrap"]
        assert len(bodies) == len(set(bodies))

    def test_shared_fragment_reports_both_tells(self):
        plan = patch_plan(PROFILES[0])
        covers = [key for patch in plan.patches for key in patch.covers]
        assert len(covers) == len(set(covers)), "a tell must not be claimed twice"
        assert set(covers) <= {t.key for t in CDP_TELLS if t.patchable}

    def test_plan_is_deterministic(self):
        a = patch_plan(PROFILES[0], seed="s")
        b = patch_plan(PROFILES[0], seed="s")
        assert a.seed == b.seed
        assert a.strategy == b.strategy
        assert patch_js(a) == patch_js(b)

    def test_strategy_varies_with_seed(self):
        strategies = {patch_plan(PROFILES[0], seed=i).strategy for i in range(8)}
        assert strategies == {"delete", "redefine"}

    def test_delegated_and_profile_fixed_tells_never_get_js(self):
        plan = patch_plan(PROFILES[0])
        covered = set(plan.covered_tells())
        assert set(_DELEGATED) <= covered
        assert set(_PROFILE_FIXED) & covered == set()

    def test_plan_reports_what_js_cannot_fix(self):
        plan = patch_plan(PROFILES[0])
        assert plan.driver_fixed == DRIVER_FIXED
        assert "runtime_enable" in plan.driver_fixed

    def test_script_installs_native_disguise(self):
        script = patch_js(patch_plan(PROFILES[0]))
        assert "[native code]" in script
        assert "Function.prototype.toString" in script

    def test_composed_script_installs_one_bootstrap(self):
        plan = patch_plan(PROFILES[0])
        spec = property_spec(PROFILES[0])
        script = full_script(plan, spec)
        assert script.count("const _nativeSrc") == 1
        assert script.count(BOOTSTRAP) == 1
        assert script.startswith("(() => {")
        assert script.rstrip().endswith("})();")

    def test_script_pollutes_no_globals(self):
        """A helper hung off `window` would be a unique token to key on."""
        script = patch_js(patch_plan(PROFILES[0]))
        assert "__omk" not in script
        assert "window.native = " not in script

    def test_probe_script_covers_every_tell(self):
        js = cdp_probe_script()
        for tell in CDP_TELLS:
            assert f"{tell.key}:" in js

    def test_audit_flags_injected_leaks(self):
        surface = {"webdriver": True, "cdc_globals": True, "playwright_bindings": True}
        keys = [leak.key for leak in audit_cdp(surface)]
        assert set(keys) == set(surface)
        assert audit_cdp(surface)[0].severity == "high"

    def test_audit_treats_truthy_non_bools_as_leaks(self):
        assert [leak.key for leak in audit_cdp({"webdriver": "yes"})] == ["webdriver"]

    def test_audit_ignores_unknown_and_falsy_keys(self):
        assert audit_cdp({"webdriver": False, "not_a_tell": True}) == []

    def test_clean_surface_has_no_leaks(self):
        assert audit_cdp({tell.key: False for tell in CDP_TELLS}) == []

    def test_patch_body_excludes_bootstrap(self):
        assert "const _nativeSrc" not in patch_body(patch_plan(PROFILES[0]))


# ── Browser property layer (AC-6) ────────────────────────────────────────


class TestBrowserPropsLayer:
    def test_spec_is_deterministic(self):
        a = property_spec(PROFILES[0], seed="p")
        b = property_spec(PROFILES[0], seed="p")
        assert a == b
        assert spoof_script(a) == spoof_script(b)

    def test_different_seeds_differ(self):
        a = property_spec(PROFILES[0], seed="p1")
        b = property_spec(PROFILES[0], seed="p2")
        assert (a.screen, a.canvas_noise) != (b.screen, b.canvas_noise)

    def test_screen_is_never_smaller_than_viewport(self):
        """The invariant detectors actually check, guaranteed by construction."""
        for profile in PROFILES:
            for seed in range(6):
                spec = property_spec(profile, seed=seed)
                assert spec.screen[0] >= profile.viewport[0]
                assert spec.screen[1] >= profile.viewport[1]

    def test_avail_is_between_viewport_and_screen(self):
        for profile in PROFILES:
            spec = property_spec(profile, seed=1)
            assert profile.viewport[1] <= spec.avail[1] <= spec.screen[1]

    def test_languages_agree_with_accept_language(self):
        profile = PROFILES[0]
        spec = property_spec(profile)
        assert spec.languages[0] == profile.locale
        for tag in profile.accept_language.split(","):
            assert tag.split(";")[0].strip() in spec.languages

    def test_timezone_comes_from_profile(self):
        for profile in PROFILES:
            assert property_spec(profile).timezone == profile.timezone_id

    def test_desktop_profiles_have_no_touch_points(self):
        for profile in PROFILES:
            if not profile.mobile and profile.platform_os not in ("Android", "iOS"):
                assert property_spec(profile).max_touch_points == 0

    def test_webgl_renderer_matches_platform(self):
        for profile in PROFILES:
            renderer = webgl_identity(profile).unmasked_renderer
            if profile.platform_os == "macOS":
                assert "Apple" in renderer
            elif profile.platform_os == "Windows":
                assert "ANGLE" in renderer
            assert "SwiftShader" not in renderer

    def test_spoof_script_renders_derived_values(self):
        spec = property_spec(PROFILES[0], seed=3)
        script = spoof_script(spec)
        assert spec.platform in script
        assert spec.timezone in script
        assert str(spec.screen[0]) in script
        assert str(spec.canvas_noise) in script

    def test_spoof_script_guards_against_software_renderer(self):
        script = spoof_script(property_spec(PROFILES[0]))
        assert "SwiftShader" not in script.split("37446")[0]  # only the real value is set
        assert "getParameter" in script

    def test_audit_catches_platform_mismatch(self):
        profile = PROFILES[0]
        expected = property_spec(profile).platform
        wrong = "Linux x86_64" if expected != "Linux x86_64" else "Win32"
        assert any("platform" in issue for issue in audit_props({"platform": wrong}, profile))

    def test_audit_catches_timezone_mismatch(self):
        issues = audit_props({"timezone": "UTC"}, PROFILES[0])
        assert any("timeZone" in issue for issue in issues)

    def test_audit_catches_language_mismatch(self):
        issues = audit_props({"languages": ["en-US", "en"]}, PROFILES[0])
        assert any("languages[0]" in issue for issue in issues)

    def test_audit_catches_screen_smaller_than_viewport(self):
        issues = audit_props({"screen": [800, 600], "viewport": [1920, 1080]}, PROFILES[0])
        assert any("smaller than viewport" in issue for issue in issues)

    def test_audit_catches_implausible_renderer(self):
        issues = audit_props({"webgl_renderer": "SwiftShader Device"}, PROFILES[0])
        assert any("not plausible" in issue for issue in issues)

    def test_audit_catches_touch_point_confusion(self):
        profile = PROFILES[0]
        issues = audit_props({"max_touch_points": 5}, profile)
        assert any("maxTouchPoints" in issue for issue in issues)

    def test_audit_accepts_its_own_spec(self):
        for profile in PROFILES:
            spec = property_spec(profile, seed=2)
            assert (
                audit_props(
                    {
                        "platform": spec.platform,
                        "languages": list(spec.languages),
                        "timezone": spec.timezone,
                        "screen": list(spec.screen),
                        "avail": list(spec.avail),
                        "viewport": list(profile.viewport),
                        "max_touch_points": spec.max_touch_points,
                        "webgl_renderer": spec.webgl.unmasked_renderer,
                    },
                    profile,
                )
                == []
            ), profile.name

    def test_partial_observation_only_judges_present_keys(self):
        assert audit_props({}, PROFILES[0]) == []


# ── Behavior extensions (AC-7) ───────────────────────────────────────────


class TestBehaviorExtensions:
    def test_typing_plan_is_deterministic(self):
        a = BehaviorClock("t").typing_plan("hello world")
        b = BehaviorClock("t").typing_plan("hello world")
        assert a == b

    def test_typing_plan_reproduces_the_text(self):
        plan = BehaviorClock("t2").typing_plan("abc def")
        typed = [k.char for k in plan if k.char != "\b"]
        assert "".join(typed) == "abc def"

    def test_typing_plan_keeps_timings_plausible(self):
        for keystroke in BehaviorClock(7).typing_plan("the quick brown fox" * 4):
            assert 35 <= keystroke.dwell_ms <= 220
            assert 0 <= keystroke.flight_ms <= 520

    def test_typos_are_followed_by_a_correction(self):
        plan = BehaviorClock("typo").typing_plan("a" * 600)
        backspaces = [k for k in plan if k.char == "\b"]
        assert backspaces, "a 600-char run should produce at least one correction"
        assert all(k.correction for k in backspaces)

    def test_zero_typo_rate_types_exactly(self):
        plan = BehaviorClock("clean").typing_plan("exact", typo_rate=0.0)
        assert [k.char for k in plan] == list("exact")

    def test_last_keystroke_has_no_flight(self):
        plan = BehaviorClock("end").typing_plan("ab")
        assert plan[-1].flight_ms == 0

    def test_empty_text_is_empty_plan(self):
        assert BehaviorClock("e").typing_plan("") == []

    def test_session_rhythm_sums_to_the_page_count(self):
        for n in (0, 1, 7, 20, 53):
            phases = BehaviorClock("r").session_rhythm(n)
            assert sum(p.pages for p in phases) == n

    def test_session_rhythm_alternates_burst_and_pause(self):
        phases = BehaviorClock("r2").session_rhythm(30)
        assert phases[0].kind == "burst"
        assert phases[-1].kind == "burst", "a session should not end on a pause"
        kinds = [p.kind for p in phases]
        assert kinds == ["burst" if i % 2 == 0 else "pause" for i in range(len(kinds))]

    def test_session_pauses_are_real_waits(self):
        pauses = [p for p in BehaviorClock("r3").session_rhythm(40) if p.kind == "pause"]
        assert pauses and all(p.duration_s >= 8.0 for p in pauses)

    def test_session_rhythm_is_deterministic(self):
        assert BehaviorClock("r4").session_rhythm(25) == BehaviorClock("r4").session_rhythm(25)

    def test_original_behavior_api_is_unchanged(self):
        """v2.12 callers must keep working — the extensions are additive."""
        clock = BehaviorClock("compat")
        assert 0.4 <= clock.think_time() <= 4.0
        assert clock.scroll_plan(8000, 1000)[-1] == 7000
        assert list(clock.mouse_path((0, 0), (10, 10))[0]) == [0, 0]
        assert isinstance(BehaviorClock("compat").typing_plan("x")[0], Keystroke)
        assert isinstance(BehaviorClock("compat").session_rhythm(4)[0], SessionPhase)


# ── CAPTCHA policy (AC-8) ────────────────────────────────────────────────


_TURNSTILE = (
    '<div class="cf-turnstile" data-sitekey="0x4AAA1111bbbb"></div>'
    '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
)
_RECAPTCHA_V3 = '<script>grecaptcha.execute("6LcAAAAAAAA", {action: "login"})</script>'
_IMAGE_GRID = '<div id="rc-imageselect" class="rc-imageselect-table">Select all images</div>'
_PRESS_HOLD = '<div id="px-captcha">Press and hold the button</div>'
_SLIDER = '<div class="geetest_slider">Drag the slider to verify</div>'
_KASADA = '<script src="/ips.js?x-kpsdk-v=j"></script>'
_AWS = "<script>window.awsWafCookieDomainList=[];aws-waf-token</script>"
_GENERIC_CHALLENGE = "<html><body>Checking your browser before accessing</body></html>"
_BENIGN = "<html><body><h1>Hello, ordinary page</h1></body></html>"


class TestCaptchaClassification:
    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            (_TURNSTILE, CaptchaKind.TURNSTILE),
            (_RECAPTCHA_V3, CaptchaKind.RECAPTCHA_V3),
            (_IMAGE_GRID, CaptchaKind.IMAGE_GRID),
            (_PRESS_HOLD, CaptchaKind.PRESS_HOLD),
            (_SLIDER, CaptchaKind.SLIDER),
            (_KASADA, CaptchaKind.KASADA),
            (_AWS, CaptchaKind.AWS_WAF),
        ],
    )
    def test_recognizes_each_family(self, html, expected):
        assert classify_captcha(html).kind is expected

    def test_benign_page_is_none_not_unknown(self):
        """A page with no challenge must not be mistaken for an unclassifiable one."""
        challenge = classify_captcha(_BENIGN)
        assert challenge.kind is CaptchaKind.NONE
        assert not challenge.requires_human
        assert not challenge.resoluble_by_clearance

    def test_generic_challenge_language_is_unknown(self):
        challenge = classify_captcha(_GENERIC_CHALLENGE)
        assert challenge.kind is CaptchaKind.UNKNOWN
        assert challenge.requires_human
        assert challenge.confidence == "low"

    def test_empty_input_is_none_and_never_raises(self):
        for value in (None, "", "<html></html>"):
            assert classify_captcha(value).kind is CaptchaKind.NONE

    def test_sitekey_and_action_are_extracted(self):
        challenge = classify_captcha(_TURNSTILE)
        assert challenge.sitekey == "0x4AAA1111bbbb"
        assert classify_captcha(_RECAPTCHA_V3).action == "login"

    def test_image_grid_wins_over_its_embedding_vendor(self):
        """An interactive widget inside a vendor frame is still interactive."""
        combined = (
            '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
            + _IMAGE_GRID
        )
        assert classify_captcha(combined).kind is CaptchaKind.IMAGE_GRID

    def test_human_and_clearance_flags_are_structural(self):
        assert classify_captcha(_IMAGE_GRID).requires_human
        assert not classify_captcha(_TURNSTILE).requires_human
        assert classify_captcha(_TURNSTILE).resoluble_by_clearance
        assert not classify_captcha(_IMAGE_GRID).resoluble_by_clearance

    def test_challenge_serializes(self):
        assert json.dumps(classify_captcha(_TURNSTILE).to_dict())


class TestCaptchaPolicy:
    @pytest.mark.parametrize("html", [_IMAGE_GRID, _PRESS_HOLD, _SLIDER, _GENERIC_CHALLENGE])
    def test_human_judgement_is_refused(self, html):
        plan = resolve_challenge(classify_captcha(html), "https://x.test")
        assert plan.action == "refuse"
        assert plan.refused
        assert plan.reason

    def test_refusal_ignores_available_solvers(self):
        """No configuration mistake may route an image grid to a solver."""
        grid = classify_captcha(_IMAGE_GRID)
        plan = resolve_challenge(
            grid, "https://x.test", backends=[MockSolver()], prefer_solver=True
        )
        assert plan.action == "refuse"
        assert plan.backend is None

    def test_clearable_kinds_route_to_clearance_by_default(self):
        for html in (_TURNSTILE, _KASADA, _AWS, _RECAPTCHA_V3):
            plan = resolve_challenge(classify_captcha(html), "https://x.test")
            assert plan.action == "clearance_flow", html

    def test_no_challenge_proceeds(self):
        plan = resolve_challenge(classify_captcha(_BENIGN), "https://x.test")
        assert plan.action == "proceed"

    def test_solver_requires_opt_in(self):
        challenge = classify_captcha(_TURNSTILE)
        conservative = resolve_challenge(challenge, "https://x.test", backends=[MockSolver()])
        opted_in = resolve_challenge(
            challenge, "https://x.test", backends=[MockSolver()], prefer_solver=True
        )
        assert conservative.action == "clearance_flow"
        assert opted_in.action == "solver"
        assert opted_in.backend == "mock"

    def test_opt_in_without_a_usable_backend_falls_back(self):
        plan = resolve_challenge(
            classify_captcha(_TURNSTILE),
            "https://x.test",
            backends=[NullSolver()],
            prefer_solver=True,
        )
        assert plan.action == "clearance_flow"

    def test_plan_serializes(self):
        assert json.dumps(resolve_challenge(classify_captcha(_TURNSTILE), "https://x.test").to_dict())


class TestSolverBackends:
    def test_null_solver_fails_closed(self):
        solver = NullSolver()
        assert not solver.available()
        result = solver.solve(classify_captcha(_TURNSTILE), "https://x.test")
        assert result.status.value == "no_credential"
        assert not result.ok
        assert "OMK_CAPTCHA_ENDPOINT" in (result.error or "")

    def test_mock_solver_is_deterministic(self):
        solver = MockSolver()
        challenge = classify_captcha(_TURNSTILE)
        a = solver.solve(challenge, "https://x.test")
        b = solver.solve(challenge, "https://x.test")
        assert a.token == b.token
        assert a.ok
        assert (a.token or "").startswith("mock-")

    def test_mock_solver_refuses_human_kinds(self):
        solver = MockSolver()
        result = solver.solve(classify_captcha(_IMAGE_GRID), "https://x.test")
        assert result.status.value == "unsupported"
        assert not result.ok

    def test_http_solver_is_inert_without_credentials(self, monkeypatch):
        monkeypatch.delenv("OMK_CAPTCHA_ENDPOINT", raising=False)
        monkeypatch.delenv("OMK_CAPTCHA_KEY", raising=False)
        solver = HttpSolver()
        assert not solver.available()
        result = solver.solve(classify_captcha(_TURNSTILE), "https://x.test")
        assert result.status.value == "no_credential"

    def test_http_solver_needs_both_halves(self, monkeypatch):
        monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", "https://solver.invalid")
        monkeypatch.delenv("OMK_CAPTCHA_KEY", raising=False)
        assert not HttpSolver().available()

    def test_http_solver_never_raises_on_transport_failure(self, monkeypatch):
        monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", "http://127.0.0.1:1/never")
        monkeypatch.setenv("OMK_CAPTCHA_KEY", "test-key")
        result = HttpSolver(timeout=0.5).solve(classify_captcha(_TURNSTILE), "https://x.test")
        assert result.status.value == "failed"
        assert not result.ok

    def test_no_credentials_are_hardcoded(self):
        """P2: the module must be inert in a clean environment."""
        import inspect

        import omk_crawl.captcha as captcha_module

        source = inspect.getsource(captcha_module)
        assert "api_key: str | None = None" in source
        assert not any(solver.available() for solver in DEFAULT_BACKENDS if solver.name == "http")

    def test_solver_result_serializes(self):
        result = MockSolver().solve(classify_captcha(_TURNSTILE), "https://x.test")
        assert json.dumps(result.to_dict())
        assert result.to_dict()["has_token"] is True


# ── Evasion arbiter (AC-9) ───────────────────────────────────────────────


class TestEvasionPlan:
    def test_plan_is_deterministic_per_site(self):
        a = plan_for("https://example.com/a")
        b = plan_for("https://example.com/b")
        assert a.seed == b.seed, "one site must present one identity across its pages"
        assert a.profile.name == b.profile.name
        assert a.init_script() == b.init_script()
        assert a.props.canvas_noise == b.props.canvas_noise

    def test_different_sites_get_different_seeds(self):
        assert plan_for("https://a.example").seed != plan_for("https://b.example").seed

    def test_salt_changes_the_identity_space(self):
        names = {plan_for("https://example.com", salt=str(i)).profile.name for i in range(24)}
        assert len(names) > 1

    def test_explicit_seed_overrides(self):
        assert plan_for("https://example.com", seed=1).seed == 1

    def test_all_layers_come_from_one_profile(self):
        plan = plan_for("https://example.com")
        assert plan.tcp.os_family.lower() == plan.profile.platform_os.lower()
        assert plan.hello.has_grease == (plan.profile.family in ("chrome", "edge"))
        assert plan.cdp.profile == plan.profile.name
        assert plan.props.profile == plan.profile.name
        assert plan.props.timezone == plan.profile.timezone_id

    def test_builtin_plans_are_coherent(self):
        for i in range(12):
            plan = plan_for("https://example.com", salt=f"s{i}")
            report = plan.coherence_report()
            assert report.ok, (plan.profile.name, report.to_dict())

    def test_coherence_report_names_every_layer(self):
        layers = [layer.layer for layer in plan_for("https://x.test").coherence_report().layers]
        assert layers == ["headers", "tls", "tcp", "js"]

    def test_coherence_report_surfaces_notes_for_unfixable_tells(self):
        report = plan_for("https://x.test").coherence_report()
        assert any("runtime_enable" in note for note in report.notes)

    def test_browser_kwargs_tell_one_story(self):
        plan = plan_for("https://x.test")
        kwargs = plan.browser_kwargs()
        assert kwargs["user_agent"] == plan.profile.user_agent
        assert kwargs["locale"] == plan.profile.locale
        assert kwargs["timezone_id"] == plan.profile.timezone_id

    def test_init_script_covers_both_surfaces(self):
        script = plan_for("https://x.test").init_script()
        assert script.count("const _nativeSrc") == 1
        assert "webdriver" in script
        assert "getParameter" in script
        assert "plugins" in script

    def test_curl_kwargs_agree_with_headers(self):
        plan = plan_for("https://x.test")
        kwargs = plan.curl_kwargs()
        assert kwargs["impersonate"] == plan.profile.impersonate
        assert kwargs["headers"]["User-Agent"] == plan.profile.user_agent
        assert plan.profile.coherence_issues(kwargs["headers"]) == []

    def test_metadata_is_json_safe(self):
        meta = plan_for("https://x.test").as_metadata()
        assert json.dumps(meta)
        assert meta["coherent"] is True
        assert meta["cdp_covered"] > 0

    def test_describe_is_human_readable(self):
        text = plan_for("https://x.test").describe()
        for expected in ("profile", "tls", "tcp", "js", "cdp", "coherent"):
            assert expected in text

    def test_captcha_policy_is_reachable_from_the_plan(self):
        plan = plan_for("https://x.test")
        assert plan.captcha_policy(_BENIGN).action == "proceed"
        assert plan.captcha_policy(_IMAGE_GRID).action == "refuse"


# ── Mock detector (AC-10) ────────────────────────────────────────────────


class TestMockDetector:
    def test_omk_evasion_scores_a_clean_sweep(self):
        verdict = score(evasion_surface())
        assert verdict.clean, verdict.to_dict()
        assert verdict.score == 1.0
        assert not verdict.unscored

    def test_evasion_beats_naive_stealth(self):
        """The research claim, checked rather than asserted: gluing browser
        headers onto a non-browser stack is worse than an honest client."""
        assert score(evasion_surface()).score > score(naive_stealth_surface()).score

    def test_naive_stealth_is_worse_than_stock(self):
        assert score(naive_stealth_surface()).score < score(stock_surface()).score

    def test_stock_client_is_detected(self):
        verdict = score(stock_surface())
        assert not verdict.clean
        assert "tls_family" in verdict.detected
        assert "behavior_cadence" in verdict.detected

    def test_missing_js_is_unscored_not_passed(self):
        assert "js_surface" in score(stock_surface()).unscored

    def test_naive_stealth_fails_every_major_axis(self):
        verdict = score(naive_stealth_surface())
        assert {"header_coherence", "tls_family", "tcp_os", "js_surface", "cdp_surface"} <= set(
            verdict.detected
        )

    def test_constant_cadence_is_flagged(self):
        from omk_crawl.verify import BehaviorSummary

        flat = BehaviorSummary(think_times=(1.0,) * 6, inter_request=(1.0,) * 6)
        assert any("metronome" in issue for issue in flat.issues())

    def test_jittered_cadence_passes(self):
        from omk_crawl.verify import BehaviorSummary

        varied = BehaviorSummary(
            think_times=(0.4, 2.9, 1.1, 3.8, 0.7, 2.2),
            inter_request=(1.0, 4.6, 0.8, 2.1, 5.3, 1.4),
        )
        assert varied.issues() == []

    def test_interactive_challenge_is_refused_by_policy(self):
        surface = evasion_surface()
        surface.challenge_html = _IMAGE_GRID
        assert score(surface).clean

    def test_score_is_bounded_and_serializable(self):
        for surface in (stock_surface(), naive_stealth_surface(), evasion_surface()):
            verdict = score(surface)
            assert 0.0 <= verdict.score <= 1.0
            assert json.dumps(verdict.to_dict())

    def test_scoring_never_raises_on_a_broken_surface(self):
        from omk_crawl.verify import RequestSurface

        assert score(RequestSurface(headers={"User-Agent": "\x00bad"})).score >= 0.0

    def test_evasion_surface_probes_every_tell(self):
        surface = evasion_surface()
        assert surface.cdp is not None
        assert set(surface.cdp) == {tell.key for tell in CDP_TELLS}


# ─ Real browser check (skipped when no browser build is present) ─────────


def _browser_available() -> bool:
    """Whether a Chromium build can actually be launched here."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            browser.close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _browser_available(), reason="no launchable Chromium build")
class TestRealBrowser:
    """End-to-end evidence that the composed script works in a real engine.

    Runs entirely against ``about:blank`` — no network, so it stays inside the
    offline-suite rule (P6) while still proving the patches execute.
    """

    def test_patches_clear_the_probe_surface(self):
        from playwright.sync_api import sync_playwright

        from omk_crawl.browser_props import probe_script as props_probe

        plan = plan_for("https://example.com")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(**plan.browser_kwargs())
                context.add_init_script(plan.init_script())
                page = context.new_page()
                page.goto("about:blank")
                cdp = page.evaluate(cdp_probe_script())
                props = page.evaluate(props_probe())
            finally:
                browser.close()

        assert audit_cdp(cdp) == [], cdp
        assert audit_props(props, plan.profile) == [], props

    def test_patched_functions_look_native(self):
        from playwright.sync_api import sync_playwright

        plan = plan_for("https://example.com")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(**plan.browser_kwargs())
                context.add_init_script(plan.init_script())
                page = context.new_page()
                page.goto("about:blank")
                observed = page.evaluate(
                    "(() => ({"
                    " patched: Function.prototype.toString.call("
                    "   WebGLRenderingContext.prototype.getParameter),"
                    " untouched: Function.prototype.toString.call(Array.prototype.map),"
                    " leaked_globals: Object.getOwnPropertyNames(window)"
                    "   .filter(k => /^(__omk|_nativeSrc|nativeToString)/.test(k)),"
                    "}))()"
                )
            finally:
                browser.close()

        assert observed["patched"] == "function getParameter() { [native code] }"
        assert "[native code]" in observed["untouched"]
        assert observed["leaked_globals"] == []

    def test_canvas_noise_is_stable_per_seed_and_absent_by_default(self):
        from playwright.sync_api import sync_playwright

        canvas_js = (
            "(() => { const c = document.createElement('canvas'); c.width = 120; c.height = 40;"
            " const x = c.getContext('2d'); x.fillStyle = '#f60'; x.fillRect(5,5,40,20);"
            " const d = x.getImageData(0,0,120,40).data; let h = 0;"
            " for (let i = 0; i < d.length; i++) { h = (h * 31 + d[i]) >>> 0; } return h; })()"
        )
        plan = plan_for("https://example.com")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                hashes = []
                for _ in range(2):
                    context = browser.new_context(**plan.browser_kwargs())
                    context.add_init_script(plan.init_script())
                    page = context.new_page()
                    page.goto("about:blank")
                    hashes.append(page.evaluate(canvas_js))
                plain = browser.new_page()
                plain.goto("about:blank")
                unpatched = plain.evaluate(canvas_js)
            finally:
                browser.close()

        assert hashes[0] == hashes[1], "the same seed must not drift between sessions"
        assert hashes[0] != unpatched, "the patch must actually perturb the canvas"


# ── Platform and transport robustness ────────────────────────────────────
# These exercise the paths that only differ on another operating system or
# against a real endpoint. The socket cases stand in for platform variance: an
# option the kernel refuses must surface as data, never as an exception.


class TestTcpPathRobustness:
    def test_every_signature_field_is_accounted_for(self):
        """A signature field in no bucket would be silently ignored (P2)."""
        accounting = signature_accounting()
        for stack in STACKS:
            for field_name in stack.syn_signature():
                assert field_name in accounting, (stack.name, field_name)

    def test_accounting_buckets_match_the_implementation(self):
        accounting = signature_accounting()
        assert accounting["ttl"] == "applied"
        assert accounting["mss"] == "applied"
        assert accounting["window"] == "proxied:rcvbuf"
        assert accounting["sack_ok"] == "unsupported"
        assert accounting["options"] == "unsupported"
        assert accounting["name"] == "metadata"

    def test_report_exposes_the_proxy_relationship(self):
        """The window is not set; SO_RCVBUF bounds it, and the report says so."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            report = apply_to_socket(sock, STACKS[0])
        assert report.proxied == {"window": "rcvbuf"}
        assert "rcvbuf" in report.applied

    def test_udp_socket_records_tcp_option_failures(self):
        """TCP-only options are rejected on UDP; that must be data, not a crash."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            report = apply_to_socket(sock, STACKS[0])
        assert "mss" in report.errors
        assert "ttl" in report.applied, "platform-independent options still apply"
        assert not report.ok, "a partial application must not report success"

    def test_closed_socket_reports_every_field_as_failed(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.close()
        report = apply_to_socket(sock, STACKS[0])
        assert report.applied == {}
        assert report.errors
        assert not report.ok

    def test_apply_never_raises_for_any_stack(self):
        for stack in STACKS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                apply_to_socket(sock, stack)
            finally:
                sock.close()


class TestHttpSolverOverRealTransport:
    """A real HTTP round trip against a local server, not a patched urlopen.

    This verifies what a mock hides: what actually goes on the wire, and how
    each failure shape is reported back.
    """

    @staticmethod
    def _serve(handler_cls):
        import socketserver

        server = socketserver.TCPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, f"http://127.0.0.1:{server.server_address[1]}"

    def test_round_trip_and_every_failure_shape(self, monkeypatch):
        import http.server

        captured: dict[str, object] = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                captured["auth"] = self.headers.get("Authorization")
                captured["ctype"] = self.headers.get("Content-Type")
                captured["payload"] = json.loads(self.rfile.read(length) or b"{}")
                body, code = {
                    "/ok": (
                        {"token": "tok-123", "cookies": {"cf_clearance": "c"}, "cost": 0.002},
                        200,
                    ),
                    "/empty": ({"error": "rate limited"}, 200),
                    "/garbage": (None, 200),
                }.get(self.path, ({"error": "boom"}, 500))
                raw = "not json" if body is None else json.dumps(body)
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw.encode())

        server, base = self._serve(Handler)
        try:
            monkeypatch.setenv("OMK_CAPTCHA_KEY", "local-key")
            challenge = classify_captcha(_TURNSTILE)

            monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", f"{base}/ok")
            result = HttpSolver(timeout=5.0).solve(challenge, "https://target.test/page")
            assert result.ok
            assert result.token == "tok-123"
            assert result.cookies == {"cf_clearance": "c"}
            assert result.cost_estimate == 0.002
            assert captured["auth"] == "Bearer local-key"
            assert captured["ctype"] == "application/json"
            # The sitekey extracted from the challenge must reach the solver:
            # a solver that never learns which widget to solve cannot solve it.
            assert captured["payload"] == {
                "kind": "turnstile",
                "sitekey": "0x4AAA1111bbbb",
                "action": None,
                "url": "https://target.test/page",
            }

            for path in ("/empty", "/garbage", "/down"):
                monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", f"{base}{path}")
                outcome = HttpSolver(timeout=5.0).solve(challenge, "https://target.test/")
                assert outcome.status.value == "failed", path
                assert outcome.error
                assert not outcome.ok

            monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", "http://127.0.0.1:1/closed")
            unreachable = HttpSolver(timeout=2.0).solve(challenge, "https://target.test/")
            assert unreachable.status.value == "failed"
            assert "URLError" in (unreachable.error or "")
        finally:
            server.shutdown()

    def test_solver_stays_inert_when_only_the_endpoint_is_set(self, monkeypatch):
        monkeypatch.setenv("OMK_CAPTCHA_ENDPOINT", "https://solver.invalid/api")
        monkeypatch.delenv("OMK_CAPTCHA_KEY", raising=False)
        result = HttpSolver().solve(classify_captcha(_TURNSTILE), "https://target.test/")
        assert result.status.value == "no_credential"
