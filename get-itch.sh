#!/usr/bin/env bash
# get-itch.sh — one real trading day from Nasdaq: Nasdaq BX, 30 July 2019, ITCH 5.0
# (same format as Nasdaq TotalView-ITCH). 391 MB gzip download, ~1 GB unpacked.

set -euo pipefail
cd "$(dirname "$0")"

URL='https://emi.nasdaq.com/ITCH/Nasdaq%20BX%20ITCH/20190730.BX_ITCH_50.gz'
OUT=data/20190730.BX_ITCH_50

mkdir -p data
if [ -s "$OUT" ]; then
  echo "ok    $OUT already exists"
  exit 0
fi

curl -fSL --retry 3 "$URL" | gunzip > "$OUT.part" # gunzip fails on a truncated download
mv "$OUT.part" "$OUT"
ls -lh "$OUT"
