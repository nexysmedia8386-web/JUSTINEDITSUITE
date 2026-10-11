#!/bin/bash
# Removes the panel + engine + portable ffmpeg; keeps settings and analysis caches.
EXT="$HOME/Library/Application Support/Adobe/CEP/extensions/com.justin.editsuite"
ROOT="$HOME/Library/Application Support/JustinEditSuite"
[ -d "$EXT" ] && rm -rf "$EXT" && echo "  [OK] Da xoa $EXT"
[ -d "$ROOT" ] && rm -rf "$ROOT" && echo "  [OK] Da xoa $ROOT"
echo "Da go Justin EDIT Suite (giu cai dat ca nhan + cache phan tich)."
