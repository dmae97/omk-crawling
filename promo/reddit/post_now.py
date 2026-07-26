#!/usr/bin/env python3
"""Headed Playwright poster for omk-crawl Reddit promo kit.

Flow:
  1. Open Chromium (headed) → reddit.com
  2. Wait until you are logged in (or complete login yourself)
  3. Submit unique posts to configured subs with images

Usage:
  python promo/reddit/post_now.py              # default P0 set
  python promo/reddit/post_now.py --all        # all 9 (slow, not recommended same day)
  python promo/reddit/post_now.py --only webscraping,sideproject
  python promo/reddit/post_now.py --dry-run    # parse only
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POSTS = ROOT / "posts"
IMAGES = ROOT / "images"
STATE = ROOT / ".reddit_storage.json"

# day-1 safe set first
DEFAULT_KEYS = ["01_webscraping", "02_sideproject", "04_commandline"]


@dataclass
class PromoPost:
    key: str
    subreddit: str
    title: str
    body: str
    image: Path | None
    source: Path


def _extract(md_path: Path) -> PromoPost:
    text = md_path.read_text(encoding="utf-8")
    # subreddit from first heading or filename
    sub = ""
    m = re.search(r"#\s*r/([A-Za-z0-9_]+)", text)
    if m:
        sub = m.group(1)
    # title: first fenced title-like line after ## Title
    title = ""
    # ## Title options → first numbered `...`
    m = re.search(r"## Title options\s*\n\s*1\.\s*`([^`]+)`", text)
    if m:
        title = m.group(1).strip()
    if not title:
        # ## Title\n`...`  or ## Title\n\n`...`
        m = re.search(r"## Title\b[^\n]*\n+`([^`]+)`", text)
        if m:
            title = m.group(1).strip()
    if not title:
        m = re.search(r"## Title\b[^\n]*\n+(?:.*\n)*?`([^`]+)`", text)
        if m:
            title = m.group(1).strip()
    if not title:
        # first markdown heading that is not r/xxx meta
        for line in text.splitlines():
            if line.startswith("# ") and not re.match(r"#\s*r/", line, re.I):
                cand = line[2:].strip()
                if len(cand) > 20:  # avoid short junk
                    title = cand
                    break
    # body: under ## Body until ## Image or ## Flair or ## Notes or ## Tone
    body = ""
    m = re.search(
        r"## Body[^\n]*\n(.*?)(?=\n## (?:Image|Flair|Notes|Tone|Where)\b|\Z)",
        text,
        re.S,
    )
    if m:
        body = m.group(1).strip()
        # drop leading affiliation-only meta lines that are instructions
        lines = []
        for ln in body.splitlines():
            if ln.strip().startswith("**Where:**"):
                continue
            lines.append(ln)
        body = "\n".join(lines).strip()
    # image
    image = None
    m = re.search(r"`images/([^`]+)`", text)
    if m:
        cand = IMAGES / m.group(1).split("/")[-1]
        if cand.is_file():
            image = cand
    if image is None:
        # fallbacks by key
        for name in ("card_router.png", "card_hero.png", "card_cli.png", "card_stack.png"):
            if (IMAGES / name).is_file():
                image = IMAGES / name
                break
    if not sub:
        raise ValueError(f"no subreddit in {md_path}")
    if not title:
        raise ValueError(f"no title in {md_path}")
    if not body:
        raise ValueError(f"no body in {md_path}")
    # Reddit title limit 300
    title = title[:300]
    return PromoPost(
        key=md_path.stem,
        subreddit=sub,
        title=title,
        body=body,
        image=image,
        source=md_path,
    )


def load_posts(keys: list[str]) -> list[PromoPost]:
    out: list[PromoPost] = []
    files = sorted(POSTS.glob("*.md"))
    files = [f for f in files if f.name != "INDEX.md"]
    for key in keys:
        k = key.lower().removesuffix(".md")
        match = None
        for f in files:
            stem = f.stem.lower()
            if k == stem or k in stem or stem.startswith(k):
                match = f
                break
        if not match:
            raise SystemExit(f"unknown post key: {key}")
        out.append(_extract(match))
    return out


def wait_for_login(page, timeout_s: float = 300.0) -> None:
    """User completes login in the headed window."""
    page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")
    print("\n>>> Log into Reddit in the opened browser window.")
    print(">>> Waiting up to {:.0f}s for a logged-in session...\n".format(timeout_s))
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            # cookie or UI signals
            cookies = page.context.cookies()
            names = {c.get("name") for c in cookies if "reddit" in (c.get("domain") or "")}
            # reddit_session is the usual auth cookie
            if "reddit_session" in names or "token_v2" in names:
                # confirm not on login
                url = page.url
                if "login" not in url:
                    print("Login detected via cookies.")
                    return
            # try navigate home and look for user menu
            if "login" not in page.url:
                # create button or user drawer
                if page.locator("header").count() > 0:
                    # check for avatar / user button
                    for sel in (
                        'button[id*="expand-user-drawer"]',
                        'button:has-text("Create")',
                        "#email-collection-tooltip-id",
                        'a[href*="/user/"]',
                        "faceplate-tracker[source='user_drawer']",
                    ):
                        try:
                            if page.locator(sel).count() > 0:
                                print(f"Login detected via UI ({sel}).")
                                return
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError("Timed out waiting for Reddit login")


def submit_post(page, post: PromoPost, *, dry: bool = False) -> str:
    print(f"\n=== r/{post.subreddit}: {post.title[:80]}")
    submit_url = f"https://www.reddit.com/r/{post.subreddit}/submit/?type=IMAGE" if post.image else f"https://www.reddit.com/r/{post.subreddit}/submit/?type=TEXT"
    # Prefer image post when image exists; fallback text
    page.goto(submit_url, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    # dismiss cookie / NSFW / onboarding modals if any
    for label in ("Accept all", "Accept", "I agree", "Continue", "Got it"):
        try:
            btn = page.get_by_role("button", name=label)
            if btn.count() and btn.first.is_visible():
                btn.first.click(timeout=1500)
                time.sleep(0.5)
        except Exception:
            pass

    # switch to image tab if needed
    if post.image:
        for name in ("Images & Video", "Image", "Images"):
            try:
                tab = page.get_by_role("button", name=name)
                if tab.count():
                    tab.first.click(timeout=2000)
                    time.sleep(1)
                    break
            except Exception:
                pass
            try:
                tab = page.get_by_text(name, exact=False)
                if tab.count():
                    tab.first.click(timeout=2000)
                    time.sleep(1)
                    break
            except Exception:
                pass

    # Title
    title_filled = False
    for sel in (
        'textarea[name="title"]',
        'div[data-contents="true"] textarea',
        'div[contenteditable="true"][aria-label*="Title" i]',
        'textarea[placeholder*="Title" i]',
        'input[name="title"]',
        "#innerTextArea",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                loc.fill(post.title)
                title_filled = True
                break
        except Exception:
            continue
    if not title_filled:
        # shreddit composer
        try:
            page.get_by_placeholder(re.compile("title", re.I)).first.fill(post.title)
            title_filled = True
        except Exception:
            pass
    if not title_filled:
        raise RuntimeError("Could not find title field — Reddit UI changed")

    # Body / text
    body_ok = False
    body = post.body
    # image posts often have optional text body
    for sel in (
        'div[contenteditable="true"][aria-label*="body" i]',
        'div[contenteditable="true"][aria-label*="text" i]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'textarea[name="text"]',
        'div[role="textbox"]',
    ):
        try:
            locs = page.locator(sel)
            n = locs.count()
            for i in range(n):
                el = locs.nth(i)
                if not el.is_visible():
                    continue
                # skip title box
                aria = (el.get_attribute("aria-label") or "").lower()
                if "title" in aria:
                    continue
                el.click()
                el.fill(body)
                body_ok = True
                break
            if body_ok:
                break
        except Exception:
            continue
    if not body_ok:
        try:
            page.keyboard.press("Tab")
            page.keyboard.type(body[:5000], delay=1)
            body_ok = True
        except Exception:
            pass

    # Upload image
    if post.image and post.image.is_file():
        uploaded = False
        for sel in ('input[type="file"]', 'input[accept*="image"]'):
            try:
                inp = page.locator(sel)
                if inp.count():
                    inp.first.set_input_files(str(post.image))
                    uploaded = True
                    print(f"  uploaded {post.image.name}")
                    time.sleep(3)
                    break
            except Exception as e:
                print(f"  file input try fail: {e}")
        if not uploaded:
            print("  WARN: image input not found — posting as text only")

    if dry:
        print("  DRY-RUN: not clicking Post")
        page.screenshot(path=str(ROOT / f"dry_{post.key}.png"), full_page=True)
        return "dry-run"

    # Post button
    posted = False
    for name in ("Post", "Submit", "Save"):
        try:
            btn = page.get_by_role("button", name=name, exact=True)
            if btn.count():
                # prefer enabled Post
                for i in range(btn.count()):
                    b = btn.nth(i)
                    if b.is_visible() and b.is_enabled():
                        b.click(timeout=5000)
                        posted = True
                        break
            if posted:
                break
        except Exception:
            continue
    if not posted:
        # css fallback
        try:
            page.locator('button:has-text("Post")').last.click(timeout=5000)
            posted = True
        except Exception as e:
            raise RuntimeError(f"Post button not found/clickable: {e}") from e

    # wait navigation or success toast
    time.sleep(5)
    url = page.url
    print(f"  after submit url: {url}")
    page.screenshot(path=str(ROOT / f"after_{post.key}.png"), full_page=True)
    # if still on submit, might need confirm
    if "/submit" in url:
        time.sleep(5)
        url = page.url
        print(f"  delayed url: {url}")
    return url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", type=str, default="", help="comma keys")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--login-timeout", type=float, default=300)
    ap.add_argument("--gap", type=float, default=45, help="seconds between posts")
    args = ap.parse_args()

    if args.all:
        keys = [f.stem for f in sorted(POSTS.glob("0*.md"))]
    elif args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
    else:
        keys = DEFAULT_KEYS

    posts = load_posts(keys)
    print("Will post:")
    for p in posts:
        print(f"  r/{p.subreddit:15} | img={p.image.name if p.image else '-':16} | {p.title[:60]}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs = {
            "viewport": {"width": 1400, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        if STATE.exists():
            context_kwargs["storage_state"] = str(STATE)
            print(f"Loaded storage state {STATE}")
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # check session
        page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
        time.sleep(2)
        cookies = {c["name"] for c in context.cookies() if "reddit" in c.get("domain", "")}
        if "reddit_session" not in cookies and "token_v2" not in cookies:
            wait_for_login(page, timeout_s=args.login_timeout)
        else:
            print("Existing session cookies found.")

        context.storage_state(path=str(STATE))
        print(f"Saved session → {STATE}")

        results = []
        for i, post in enumerate(posts):
            try:
                url = submit_post(page, post, dry=args.dry_run)
                results.append((post, url, "ok"))
            except Exception as exc:
                print(f"  FAIL r/{post.subreddit}: {exc}")
                page.screenshot(path=str(ROOT / f"fail_{post.key}.png"), full_page=True)
                results.append((post, "", f"fail: {exc}"))
            if i < len(posts) - 1 and not args.dry_run:
                print(f"  sleeping {args.gap}s …")
                time.sleep(args.gap)

        context.storage_state(path=str(STATE))
        browser.close()

    print("\n===== RESULTS =====")
    for post, url, st in results:
        print(f"r/{post.subreddit}: {st} {url}")
    # write log
    log = ROOT / "post_results.md"
    lines = ["# Reddit post results", ""]
    for post, url, st in results:
        lines.append(f"- r/{post.subreddit}: **{st}** {url}")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {log}")


if __name__ == "__main__":
    main()
