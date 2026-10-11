#!/bin/bash
# Justin EDIT Suite — macOS installer. Self-healing: Python 3.12, packages, ffmpeg, AI pack (Apple Silicon),
# models are installed when missing. Safe to run again: it only fills the gaps.
#   bash install_mac.sh [--ai auto|yes|no] [--no-models]
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(head -n1 "$HERE/VERSION.txt" 2>/dev/null || echo 2.1)"
AI="auto"; MODELS="--models"
while [ $# -gt 0 ]; do
  case "$1" in
    --ai) AI="$2"; shift 2;;
    --no-models) MODELS=""; shift;;
    *) shift;;
  esac
done
ok()   { printf "  \033[32m[OK]\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m[!]\033[0m %s\n" "$1"; }
step() { printf "\n\033[36m[%s] %s\033[0m\n" "$1" "$2"; }

echo "=========================================================="
echo "   JUSTIN EDIT SUITE $VERSION - CAI DAT TREN MACOS"
echo "   Thieu gi tu tai nay - chay lai bao nhieu lan cung duoc"
echo "=========================================================="

ROOT="$HOME/Library/Application Support/JustinEditSuite"
ENGINE_DST="$ROOT/engine"
CEP_DIR="$HOME/Library/Application Support/Adobe/CEP/extensions"
EXT_DST="$CEP_DIR/com.justin.editsuite"

step 1/5 "Bat che do cho phep panel trong Premiere Pro..."
for v in $(seq 9 25); do defaults write "com.adobe.CSXS.$v" PlayerDebugMode 1 2>/dev/null; done
ok "PlayerDebugMode = 1 (CSXS 9-25)"

step 2/5 "Tim / cai Python 3.10+..."
find_py() {
  for c in python3.12 python3.11 python3.13 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    p="$(command -v "$c" 2>/dev/null || true)"
    [ -z "$p" ] && [ -x "$c" ] && p="$c"
    [ -z "$p" ] && continue
    if "$p" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then echo "$p"; return; fi
  done
}
PY="$(find_py)"
if [ -z "$PY" ]; then
  warn "Chua co Python 3.10+ -> dang cai Python 3.12..."
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.12
  else
    PKG="/tmp/python-3.12.10-macos11.pkg"
    curl -fL -o "$PKG" "https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg" && \
      echo "  Nhap mat khau may Mac de cai Python:" && sudo installer -pkg "$PKG" -target /
  fi
  PY="$(find_py)"
fi
if [ -z "$PY" ]; then
  printf "  \033[31m[X]\033[0m Khong cai duoc Python. Cai tay tai https://www.python.org/downloads/macos/ roi chay lai.\n"
  exit 1
fi
ok "Python: $PY"

step 3/5 "Chep panel va engine..."
for p in "extension/com.justin.editsuite" "engine"; do
  [ -d "$HERE/$p" ] || { printf "  \033[31m[X]\033[0m Thieu thu muc %s trong goi cai dat\n" "$p"; exit 1; }
done
mkdir -p "$CEP_DIR" "$ROOT"
rm -rf "$EXT_DST" "$ENGINE_DST"
cp -R "$HERE/extension/com.justin.editsuite" "$EXT_DST"
cp -R "$HERE/engine" "$ENGINE_DST"
xattr -dr com.apple.quarantine "$EXT_DST" "$ENGINE_DST" 2>/dev/null || true
ok "Panel  -> $EXT_DST"
ok "Engine -> $ENGINE_DST"

step 4/5 "Kiem tra va tu cai cac thanh phan con thieu (co the mat vai phut lan dau)..."
cd "$ENGINE_DST" && "$PY" -m nxcore.doctor --fix --ai "$AI" --ollama no --settings "$ENGINE_DST" $MODELS | grep -v '^DOCTOR '
RC=${PIPESTATUS[0]}

step 5/5 "Ket qua"
if [ "$RC" -eq 0 ]; then
  ok "Da du thanh phan. Mo Premiere Pro -> Window -> Extensions -> Justin EDIT Suite."
  echo "  Neu Premiere dang mo: tat han roi mo lai de nap panel moi."
else
  warn "Con thanh phan chua cai duoc (xem dong [X] o tren). Kiem tra mang roi chay lai - chi cai phan con thieu."
fi
exit "$RC"
