# App Store (iOS public metadata)

When **IPA is unavailable** (Apple DRM), use the public iTunes Lookup/Search API.

## CLI

```bash
omk-crawl 'appstore://search?q=요기요'
omk-crawl appstore://378084485
omk-crawl 'appstore://bundle?bundleId=com.jawebs.baedal'
omk-crawl ios://Freeform
```

## Python

```python
from omk_crawl.mobile import AppStoreClient

c = AppStoreClient(country="kr")
app = c.lookup(378084485)           # Baemin iOS
print(app.bundle_id, app.version)   # com.jawebs.baedal 16.15.0

for a in c.search("쿠팡이츠", limit=5):
    print(a.track_name, a.bundle_id, a.user_rating_count)
```

## IPA static (when you have a package)

```bash
omk-crawl ./app.ipa -o ipa-report.md
```

## Notes

- Does **not** download IPA binaries (FairPlay).
- Enough for bundle id, version, seller, ratings, screenshots, description head.
- Pair with APK static (`omk-crawl app.apk`) for Android API hosts.
