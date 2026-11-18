#!/usr/bin/env bash
# get-cpptrader.sh — fetch CppTrader, CppCommon and fmt into third_party/ at pinned commits,
# so the server and local builds use the same code. Only 4 CppTrader sources and the CppCommon/fmt
# headers are used, so submodules (gil, cmake) are not fetched.

set -euo pipefail
cd "$(dirname "$0")"

clone() { # URL DIR COMMIT
  local url=$1 dir=third_party/$2 sha=$3
  if [ "$(git -C "$dir" rev-parse HEAD 2>/dev/null)" = "$sha" ]; then
    echo "ok    $2 already at $sha"
    return
  fi
  rm -rf "$dir"
  git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 "$url" "$sha"
  git -C "$dir" checkout -q FETCH_HEAD
  echo "ok    $2 $sha"
}

clone https://github.com/chronoxor/CppTrader.git CppTrader d9625c83e4d592eff65281b00e3e79348d413eeb # 2026-11-06
clone https://github.com/chronoxor/CppCommon.git CppCommon b6b9927fc4cb3a7bcba499d7306961e720bdf59f # 2026-11-05
clone https://github.com/fmtlib/fmt.git fmt 4229eb5820af49bf797641168624c8a754a1a4eb             # 2026-11-11

ls -lh third_party/CppTrader/tools/itch/sample.itch
