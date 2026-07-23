#!/usr/bin/env bash
# check-versions.sh — upstream 버전 드리프트 확인
# SKILL.md에 pinned 버전과 PyPI/npm 최신을 비교한다.
set -euo pipefail

echo "=== omk-crawling upstream version check ==="
echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

check_pypi() {
  local name="$1" pinned="$2"
  local latest
  latest=$(curl -s "https://pypi.org/pypi/${name}/json" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['info']['version'])" 2>/dev/null || echo "FETCH_FAILED")
  local status="✅"
  [[ "$latest" != "$pinned" ]] && status="⚠️  DRIFT"
  printf "  %-20s pinned=%-10s latest=%-10s %s\n" "$name" "$pinned" "$latest" "$status"
}

check_npm() {
  local name="$1" pinned="$2"
  local latest
  latest=$(curl -s "https://registry.npmjs.org/${name}/latest" 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])" 2>/dev/null || echo "FETCH_FAILED")
  local status="✅"
  [[ "$latest" != "$pinned" ]] && status="⚠️  DRIFT"
  printf "  %-20s pinned=%-10s latest=%-10s %s\n" "$name" "$pinned" "$latest" "$status"
}

echo "── PyPI ──"
check_pypi "crawl4ai"     "0.9.2"
check_pypi "Scrapy"       "2.17.0"
check_pypi "crawlee"      "1.8.3"
check_pypi "browser-use"  "0.13.6"
check_pypi "curl_cffi"    "0.15.0"
check_pypi "autoscraper"  "1.1.14"
check_pypi "markitdown"   "0.1.6"
check_pypi "scrapling"    "0.4.11"

echo ""
echo "── npm ──"
check_npm "crawlee" "3.17.0"

echo ""
echo "── Native (manual check) ──"
echo "  curl-impersonate   https://github.com/lwthiker/curl-impersonate/releases"
echo "  scrcpy             https://github.com/Genymobile/scrcpy/releases  (pinned: 4.1)"

echo ""
echo "Done. Update SKILL.md + NOTICE.md if drift detected."
