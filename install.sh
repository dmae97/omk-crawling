#!/usr/bin/env bash
# install.sh — omk-crawling 스킬을 OMK에 설치
#
# Usage:
#   git clone https://github.com/user/omk-crawling.git
#   cd omk-crawling
#   ./install.sh              # symlink (개발용, 수정 즉시 반영)
#   ./install.sh --copy       # copy (안정적, 레포 수정해도 스킬 불변)
#   ./install.sh --uninstall  # 제거
set -euo pipefail

SKILL_NAME="omk-crawling"
SKILL_DIR="${OMK_SKILL_DIR:-$HOME/.omk/agent/skills/$SKILL_NAME}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# 스킬에 필요한 파일만 (Python 패키지/테스트/스크립트 제외)
SKILL_FILES=(
  SKILL.md NOTICE.md LICENSE.txt
  references examples
)

case "${1:-}" in
  --uninstall)
    if [[ -L "$SKILL_DIR" ]]; then
      rm "$SKILL_DIR"
      echo "✅ Removed symlink: $SKILL_DIR"
    elif [[ -d "$SKILL_DIR" ]]; then
      rm -rf "$SKILL_DIR"
      echo "✅ Removed: $SKILL_DIR"
    else
      echo "Nothing to remove at $SKILL_DIR"
    fi
    exit 0
    ;;

  --copy)
    rm -rf "$SKILL_DIR"
    mkdir -p "$SKILL_DIR"
    for f in "${SKILL_FILES[@]}"; do
      cp -r "$REPO_DIR/$f" "$SKILL_DIR/"
    done
    echo "✅ Copied skill → $SKILL_DIR"
    echo "   Files: $(find "$SKILL_DIR" -type f | wc -l)"
    ;;

  *)
    # Symlink (default) — 레포 수정이 즉시 스킬에 반영
    if [[ -e "$SKILL_DIR" && ! -L "$SKILL_DIR" ]]; then
      echo "⚠️  $SKILL_DIR exists (not a symlink). Backing up → ${SKILL_DIR}.bak"
      mv "$SKILL_DIR" "${SKILL_DIR}.bak"
    fi
    ln -sfn "$REPO_DIR" "$SKILL_DIR"
    echo "✅ Symlinked skill → $SKILL_DIR"
    echo "   Target: $REPO_DIR"
    ;;
esac

echo ""
echo "Verify: ls $SKILL_DIR/SKILL.md"
echo "Reload: OMK 세션 재시작 시 스킬 자동 감지"
