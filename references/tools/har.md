# HAR — offline web/app capture inspection

Use an existing HAR exported by browser DevTools or an authorized app debugging
session. This adapter reads a local regular file. It does not capture traffic,
replay requests, connect to recorded endpoints, or bypass app authentication/TLS.

## CLI

```bash
omk-crawl capture.har --json -o endpoints.json
omk-crawl capture.har -o endpoints.md
omk-crawl capture.har --json --har-bodies -o responses.json
omk-crawl exported.json --tool har --json
```

`--har-bodies` requires `--json`. Markdown always contains only the endpoint summary.
Missing or invalid archives exit with code 1. Mixed valid/invalid entries produce
partial results with indexed `metadata.issues`; an entirely invalid capture fails.
An empty `log.entries` array is valid.

## Python

```python
from omk_crawl import analyze_har

result = analyze_har("capture.har", include_bodies=True, max_body_bytes=512 * 1024)
if result.ok:
    for entry in result.extracted or []:
        print(entry["method"], entry["url"], entry["status"], entry["body_state"])
else:
    print(result.error)
```

Each record retains its zero-based HAR entry `index`, HTTP method, URL without
userinfo/query/fragment, response `status`, `mime_type`, and `time_ms` (null if
missing or invalid). A response status of 0 means the capture has no HTTP response.

Default limits: 32 MiB per file, 10,000 entries, and 2 MiB per decoded response body.
Override with `max_bytes`, `max_entries`, and `max_body_bytes` in Python. Exceeding a
file or entry limit fails explicitly; exceeding a body limit preserves the endpoint.

With `include_bodies=True`, UTF-8 JSON and HAR base64-encoded JSON are decoded,
including `application/*+json`. `body_state` is `included`, `omitted`, `missing`,
`unsupported_mime`, `unsupported_encoding`, `too_large`, or `invalid_body`.
Only `included` records contain `body`. Missing capture payloads cannot be recovered;
compressed encodings other than HAR base64 are not decompressed.

## Sensitive data

Request/response headers, cookies, request post data, URL userinfo, the entire query
string, and the fragment are never exported. Response bodies are excluded by default.
This is **not an anonymizer**: URL paths, MIME fields, filenames, and opted-in JSON
bodies can still contain private data. Inspect exports before sharing. Keep source
HARs and response exports out of Git and public logs.
