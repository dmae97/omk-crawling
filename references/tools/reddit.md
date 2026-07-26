# Reddit target client

Layer **target** — listings via old.reddit JSON after cookie warmup.

## Why not www?

| Path | Result |
|------|--------|
| `www.reddit.com/...` HTML | 200 but "Please wait for verification" shell (~8KB) |
| `*.json` without cookies | 403 challenge HTML (~190KB) |
| **`old.reddit.com/` warmup → `.json`** | **200 application/json** |

## CLI

```bash
omk-crawl reddit://status
omk-crawl reddit://r/programming
omk-crawl reddit://r/programming/top?t=day&limit=10
omk-crawl 'reddit://search?q=rust+async&limit=10'
omk-crawl https://www.reddit.com/r/korea/
```

## Python

```python
from omk_crawl.reddit import RedditClient, posts_to_markdown

c = RedditClient()
res = c.subreddit("programming", sort="hot", limit=15)
print(posts_to_markdown(res.posts))
for p in res.posts[:5]:
    print(p.score, p.title)
```

## Guardrails

- Respect Reddit ToS / rate limits (default 0.5 rps).
- Public listings only; no auth bypass for private subs.
- Prefer official API + OAuth for production apps.
