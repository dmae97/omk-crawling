#!/usr/bin/env python3
"""Post promo texts via old.reddit submit (more automation-friendly).

Images are embedded as GitHub raw markdown links (already public).

  python promo/reddit/post_oldreddit.py --only 01_webscraping,02_sideproject,04_commandline
"""

from __future__ import annotations

import argparse
import sys
import time
from contextlib import suppress
from pathlib import Path

# reuse parser from post_now
sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_now import DEFAULT_KEYS, load_posts  # noqa: E402

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".reddit_storage.json"
RAW = "https://raw.githubusercontent.com/dmae97/omk-crawling/main/promo/reddit/images"


def with_image_md(body: str, image_name: str | None) -> str:
    if not image_name:
        return body
    url = f"{RAW}/{image_name}"
    block = f"\n\n![{image_name}]({url})\n"
    if url in body:
        return body
    return body.rstrip() + block


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(DEFAULT_KEYS))
    ap.add_argument("--gap", type=float, default=35)
    ap.add_argument("--login-timeout", type=float, default=420)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    keys = [k.strip() for k in args.only.split(",") if k.strip()]
    posts = load_posts(keys)
    for p in posts:
        print(f"r/{p.subreddit}: {p.title[:70]} | img={p.image.name if p.image else '-'}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx_kwargs = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "locale": "en-US",
        }
        if STATE.exists():
            ctx_kwargs["storage_state"] = str(STATE)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        page.goto("https://old.reddit.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)
        # login?
        logged = False
        # Logged-out layout has no user link; the cookie check below decides.
        with suppress(Exception):
            if page.locator("#header-bottom-right .user a").count():
                user = page.locator("#header-bottom-right .user a").first.inner_text(timeout=2000)
                if user and user.lower() not in {"log in", "login"}:
                    print("Logged in as", user)
                    logged = True
        cookies = {c.get("name") for c in context.cookies()}
        if "reddit_session" in cookies:
            logged = True
            print("reddit_session cookie present")

        if not logged:
            page.goto("https://old.reddit.com/login", wait_until="domcontentloaded")
            print("\n>>> LOGIN REQUIRED in the browser window (old.reddit)")
            print(f">>> waiting {args.login_timeout:.0f}s …\n")
            deadline = time.time() + args.login_timeout
            while time.time() < deadline:
                cookies = {c.get("name") for c in context.cookies()}
                if "reddit_session" in cookies:
                    logged = True
                    break
                # Page may still be mid-login; just retry on the next tick.
                with suppress(Exception):
                    page.goto(
                        "https://old.reddit.com/", wait_until="domcontentloaded", timeout=30000
                    )
                    if page.locator("#header-bottom-right .user a").count():
                        t = page.locator("#header-bottom-right .user a").first.inner_text(
                            timeout=1000
                        )
                        if t and "log" not in t.lower():
                            logged = True
                            print("Logged in as", t)
                            break
                time.sleep(3)
            if not logged:
                page.screenshot(path=str(ROOT / "login_timeout.png"))
                raise SystemExit("login timeout — complete login in the window and re-run")

        context.storage_state(path=str(STATE))
        results = []
        for i, post in enumerate(posts):
            img_name = post.image.name if post.image else None
            body = with_image_md(post.body, img_name)
            url = f"https://old.reddit.com/r/{post.subreddit}/submit"
            print(f"\n=== submit r/{post.subreddit}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)
            # title
            try:
                page.fill('textarea[name="title"]', post.title)
            except Exception:
                page.fill('input[name="title"]', post.title)
            # text body tab — optional: some subreddits render the editor directly
            with suppress(Exception):
                if page.locator("#text-desc").count():
                    page.click("#text-desc")
            try:
                page.fill('textarea[name="text"]', body)
            except Exception as e:
                print(" body fill fail", e)
                page.screenshot(path=str(ROOT / f"fail_body_{post.key}.png"))
                results.append((post.subreddit, "fail-body", ""))
                continue

            if args.dry_run:
                page.screenshot(path=str(ROOT / f"dry_old_{post.key}.png"))
                results.append((post.subreddit, "dry", page.url))
                continue

            # submit
            clicked = False
            for sel in (
                'button[name="submit"]',
                'button:has-text("submit")',
                ".submit-btn",
                "#submit-form button[type=submit]",
            ):
                try:
                    if page.locator(sel).count():
                        page.locator(sel).first.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                page.keyboard.press("Enter")
            time.sleep(5)
            final = page.url
            print("  ->", final)
            page.screenshot(path=str(ROOT / f"after_old_{post.key}.png"))
            ok = "/comments/" in final or "/r/" in final and "submit" not in final
            # captcha?
            if page.locator("#captcha_label, .g-recaptcha, iframe[src*='captcha']").count():
                print("  CAPTCHA detected — solve in browser, waiting 120s")
                time.sleep(120)
                final = page.url
                ok = "/comments/" in final
            results.append((post.subreddit, "ok" if ok else "maybe", final))
            context.storage_state(path=str(STATE))
            if i < len(posts) - 1:
                time.sleep(args.gap)

        browser.close()

    lines = ["# old.reddit post results", ""]
    for sub, st, url in results:
        lines.append(f"- r/{sub}: **{st}** {url}")
        print(f"r/{sub}: {st} {url}")
    (ROOT / "post_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
