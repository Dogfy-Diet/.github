#!/usr/bin/env bash
# Renders the report for the fixture (a toy project with real Vitest v8 output)
# and checks the numbers and the pieces of the comment.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$(mktemp -d)"
trap 'rm -rf "$out"' EXIT

python3 "$here/../report.py" \
  --coverage-dir "$here/fixture/coverage" \
  --repo-dir "$here/fixture/project" \
  --diff-file "$here/fixture/pr.diff" \
  --out "$out" --title toy --repository acme/toy --sha 0123456789abcdef \
  --images-url https://example.com/cards --report-url https://example.com/report >/dev/null

fail() { echo "FAIL: $*" >&2; exit 1; }
expect() { grep -qF -- "$2" "$out/$1" || fail "$1 does not contain: $2"; }

summary="$(cat "$out/summary.json")"
[ "$(jq -r .new_lines <<<"$summary")" = 11 ] || fail "new_lines: $summary"
[ "$(jq -r .new_lines_covered <<<"$summary")" = 6 ] || fail "new_lines_covered: $summary"
[ "$(jq -r .files <<<"$summary")" = 2 ] || fail "files: $summary"

for f in hero-light.svg hero-dark.svg files-light.svg files-dark.svg; do
  python3 -c "import sys, xml.dom.minidom; xml.dom.minidom.parse(sys.argv[1])" "$out/$f" || fail "$f is not valid SVG"
done

expect comment-cards.md '<!-- coverage-report -->'
expect comment-cards.md 'srcset="https://example.com/cards/hero-dark.svg"'
expect comment-cards.md 'https://example.com/report'
expect comment-plain.md 'Código nuevo de la PR: 54,5'
expect comment-plain.md '!   3 │     throw new Error'
expect comment-plain.md '[L12–15](https://github.com/acme/toy/blob/0123456789abcdef/src/shipping.js#L12-L15)'
expect comment-plain.md '| nuevo |'
if grep -q '<picture>' "$out/comment-plain.md"; then fail "comment-plain.md must not reference the cards"; fi

echo "ok: coverage-report renders the fixture"
