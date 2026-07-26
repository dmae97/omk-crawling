# Mobile surfaces — Android & iOS

Layer **⑤** of omk-crawling. Use when data is **not on the public web**.

| Target | Tool | Input |
|--------|------|--------|
| Android package (static) | `apk` | `app.apk` / `apk:///path/app.apk` |
| iOS package (static) | `ipa` | `app.ipa` / `ipa:///path/app.ipa` |
| Live Android device | `scrcpy` / `android` | `android://…` (needs `adb`) |

## CLI

```bash
# APK static surface (URLs, perms, firebase/ssl-pin hints)
omk-crawl ./app.apk -o apk-report.md
omk-crawl ./app.apk --json

# IPA static surface (bundle id, URL schemes, ATS, URLs)
omk-crawl ./app.ipa -o ipa-report.md

# Device bridge
omk-crawl android://                         # list devices
omk-crawl 'android://SERIAL/packages'        # third-party packages
omk-crawl 'android://SERIAL/dump?pkg=com.x'  # dumpsys package
omk-crawl 'android://SERIAL/activity'        # activity stack
omk-crawl 'android://SERIAL/screenshot'      # PNG via screencap
```

## Python

```python
from omk_crawl.mobile import analyze_apk, analyze_ipa, list_adb_devices
from omk_crawl.tools import get_tool

rep = analyze_apk("app.apk")
print(rep.package, rep.urls[:10])

r = get_tool("apk").fetch("app.apk")
print(r.markdown)
```

## Optional host tools

| Binary | Role |
|--------|------|
| `adb` | Required for `android://` / `scrcpy` tool |
| `scrcpy` | Interactive mirror (documented; not required for dump/screenshot) |
| `aapt` / `aapt2` | Richer APK badging (package/version/sdk) |
| `androguard` (pip) | Optional APK enrichment — `pip install omk-crawl[mobile]` |

Zip-scan path always works without aapt/androguard.

## Routing note

Mobile tools are **not** in the web `ESCALATION_CHAIN`.  
CLI auto-selects them from file suffix / `android://` scheme.  
Force with `--tool apk|ipa|scrcpy`.

## Guardrails

- Only devices/packages you own or are authorized to assess.
- USB debugging is sensitive — disable when done.
- Static analysis ≠ runtime bypass of SSL pinning; use mitm + Frida separately when authorized.
