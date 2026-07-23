#!/usr/bin/env bash
# record-demos.sh — Record terminal demos as SVG animations
set -euo pipefail
cd "$(dirname "$0")/.."
DEMO_DIR="assets/demos"
mkdir -p "$DEMO_DIR"

record() {
  local name="$1"
  shift
  local outfile="$DEMO_DIR/${name}.svg"
  echo "Recording: $name → $outfile"
  # Use script to create a pty, pipe through termtosvg
  script -qec "bash -c '$*'" /dev/null 2>/dev/null | \
    termtosvg "$outfile" -t window_frame 2>/dev/null || true
  if [[ -f "$outfile" ]]; then
    echo "  ✅ $(du -h "$outfile" | cut -f1)"
  else
    echo "  ❌ failed"
  fi
}

# Demo 1: Auto-escalation (verbose)
record "01-auto-escalation" \
  "cd /home/yu/projects/omk-crawling && python -m omk_crawl.cli https://example.com -v 2>&1 | head -10; sleep 1"

# Demo 2: Tool discovery
record "02-tool-discovery" \
  "cd /home/yu/projects/omk-crawling && python -m omk_crawl.cli --tools; sleep 1"

# Demo 3: Diagnose
record "03-diagnose" \
  "cd /home/yu/projects/omk-crawling && python -m omk_crawl.cli --diagnose https://example.com; sleep 1"

# Demo 4: JSON output
record "04-json-output" \
  "cd /home/yu/projects/omk-crawling && python -m omk_crawl.cli https://example.com --json 2>/dev/null | head -15; sleep 1"

# Demo 5: Python API
record "05-python-api" \
  "cd /home/yu/projects/omk-crawling && python3 -c \"
from omk_crawl import crawl
r = crawl('https://example.com')
print(r.summary())
print()
print(r.html[:200] if r.html else 'no content')
\"; sleep 1"

echo ""
echo "Done! Files:"
ls -lh "$DEMO_DIR"/*.svg 2>/dev/null
