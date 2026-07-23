#!/usr/bin/env python3
"""Headless browser demo — Playwright + omk-crawl pipeline."""
import time
import sys
sys.path.insert(0, ".")

def main():
    print("$ python3 demo_browser.py")
    print()
    print("  [browser] Launching headless Chrome...")
    
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        
        print("  [browser] Navigating → https://news.ycombinator.com")
        t0 = time.time()
        page.goto("https://news.ycombinator.com", wait_until="domcontentloaded")
        elapsed = (time.time() - t0) * 1000
        print(f"  [browser] ✓ Loaded in {elapsed:.0f}ms")
        print()
        
        # Extract top stories
        print("  [extract] Parsing top stories...")
        stories = page.query_selector_all("tr.athing")
        print(f"  [extract] ✓ Found {len(stories)} stories")
        print()
        
        for i, story in enumerate(stories[:5]):
            rank = story.query_selector("span.rank")
            title_el = story.query_selector("span.titleline > a")
            if title_el:
                rank_text = rank.inner_text() if rank else f"{i+1}."
                title = title_el.inner_text()
                href = title_el.get_attribute("href") or ""
                print(f"  {rank_text} {title}")
                print(f"      {href[:70]}")
        
        print()
        
        # Screenshot
        page.screenshot(path="assets/demos/browser-screenshot.png")
        print("  [screenshot] ✓ Saved → assets/demos/browser-screenshot.png")
        print()
        
        # Now use omk-crawl on the same URL
        print("  [omk-crawl] Cross-checking with SmartRouter...")
        from omk_crawl import crawl
        r = crawl("https://news.ycombinator.com")
        print(f"  [omk-crawl] {r.summary()}")
        print()
        print("  ✓ Browser + SmartRouter pipeline complete.")
        
        browser.close()

if __name__ == "__main__":
    main()
