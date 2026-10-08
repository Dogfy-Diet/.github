#!/usr/bin/env python3
"""Render a PR coverage report from Istanbul coverage output (Vitest v8/istanbul or Jest).

Reads coverage-summary.json + coverage-final.json + lcov.info, crosses them with the
PR diff and writes, into --out:

  comment-cards.md   comment with the SVG cards (needs --images-url)
  comment-plain.md   same content in plain markdown, for when the cards can't be hosted
  hero-{light,dark}.svg, files-{light,dark}.svg

Standard library only: it runs on any GitHub-hosted runner without installing anything.
"""
import argparse
import json
import math
import os
import re
import subprocess
from collections import defaultdict

NBSP = " "

# --- args ---------------------------------------------------------------------------

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--coverage-dir", required=True, help="dir with coverage-summary.json, coverage-final.json and lcov.info")
ap.add_argument("--repo-dir", default=".", help="git checkout the tests ran on (default: .)")
ap.add_argument("--base", help="base commit/ref of the PR; the diff is merge-base(base, head)..head")
ap.add_argument("--head", default="HEAD", help="head commit/ref (default: HEAD)")
ap.add_argument("--diff-file", help="read the unified diff from a file instead of git (tests)")
ap.add_argument("--base-summary", help="coverage-summary.json of the base branch, for the project deltas (optional)")
ap.add_argument("--out", required=True, help="output dir")
ap.add_argument("--title", default="", help="name shown in the header, e.g. the service")
ap.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""), help="owner/repo, for links")
ap.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""), help="commit the links point at")
ap.add_argument("--images-url", default="", help="URL prefix where the SVG cards will be served")
ap.add_argument("--report-url", default="", help="link to the full HTML report (e.g. the artifact)")
ap.add_argument("--runner", default="", help="label for the footer, e.g. 'Vitest · v8'")
ap.add_argument("--threshold", type=float, default=80, help="target for new code, in percent (default: 80)")
ap.add_argument("--marker", default="coverage-report", help="hidden marker that identifies the comment")
ap.add_argument("--max-files", type=int, default=8, help="files drawn in the card")
ap.add_argument("--max-snippets", type=int, default=6, help="files with an uncovered-code snippet")
args = ap.parse_args()

repo = os.path.realpath(args.repo_dir)
os.makedirs(args.out, exist_ok=True)
WARN = args.threshold * 0.625  # 80 → 50: below this a file is red


def git(*a):
    return subprocess.check_output(["git", "-C", repo, *a], text=True)


def rel(p):
    """Coverage tools write absolute paths of wherever the tests ran."""
    p = os.path.realpath(p) if os.path.isabs(p) else p
    return os.path.relpath(p, repo) if os.path.isabs(p) else p


def load_summary(path):
    data = json.load(open(path))
    return {("total" if k == "total" else rel(k)): v for k, v in data.items()}


# --- inputs ---------------------------------------------------------------------------

cov = args.coverage_dir
summ = load_summary(f"{cov}/coverage-summary.json")
final = {rel(k): v for k, v in json.load(open(f"{cov}/coverage-final.json")).items()}
bsumm = load_summary(args.base_summary) if args.base_summary and os.path.exists(args.base_summary) else None

hits = defaultdict(dict)  # file -> {line: hit count}, only executable lines
cur = None
for line in open(f"{cov}/lcov.info"):
    if line.startswith("SF:"):
        cur = rel(line[3:].strip())
    elif line.startswith("DA:"):
        n, c = line[3:].split(",")[:2]
        hits[cur][int(n)] = int(c)

if args.diff_file:
    diff = open(args.diff_file).read()
else:
    base = git("merge-base", args.base, args.head).strip()
    diff = git("diff", "-U0", "--no-color", "--no-renames", f"{base}..{args.head}")

added = defaultdict(set)  # file -> lines added or modified by the PR
new_files, f = set(), None
prev = None
for line in diff.splitlines():
    if line.startswith("--- "):
        prev = line[4:]
    elif line.startswith("+++ "):
        f = line[6:] if line.startswith("+++ b/") else None
        if f and prev == "/dev/null":
            new_files.add(f)
    elif line.startswith("@@") and f:
        m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
        start, count = int(m.group(1)), int(m.group(2) or 1)
        added[f].update(range(start, start + count))

# --- per-file analysis -------------------------------------------------------------------

files = []
for path, lines in added.items():
    if path not in summ:
        continue  # not instrumented: tests, types, config…
    h = hits.get(path, {})
    executable = sorted(l for l in lines if l in h)
    if not executable:
        continue  # only comments, imports of types, blank lines…
    uncovered_new = [l for l in executable if h[l] == 0]
    fc = final.get(path, {"fnMap": {}, "f": {}})
    untested_fns = []
    for k, fn in fc["fnMap"].items():
        a, b = fn["loc"]["start"]["line"], fn["loc"]["end"]["line"]
        if fc["f"].get(k) == 0 and not fn["name"].startswith("(anonymous") and any(a <= l <= b for l in lines):
            untested_fns.append(fn["name"])
    try:
        src = open(os.path.join(repo, path), encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        src = []
    s, bs = summ[path], (bsumm or {}).get(path)
    files.append(dict(
        path=path, name=os.path.basename(path), dir=os.path.dirname(path) + "/", src=src,
        total=max(len(src), max(h) if h else 1, 1),
        lines=s["lines"]["pct"], branches=s["branches"]["pct"], funcs=s["functions"]["pct"],
        delta=None if bs is None else s["lines"]["pct"] - bs["lines"]["pct"],
        new=path in new_files, executable=len(executable), covered=len(executable) - len(uncovered_new),
        uncovered_new=uncovered_new, uncovered_all=[l for l, c in h.items() if c == 0],
        added=lines, untested_fns=untested_fns,
    ))

for x in files:
    x["pct"] = 100 * x["covered"] / x["executable"]
files.sort(key=lambda x: (x["pct"], -x["executable"]))

N_exec = sum(x["executable"] for x in files)
N_cov = sum(x["covered"] for x in files)
newpct = 100 * N_cov / N_exec if N_exec else None
T = summ["total"]
B = bsumm["total"] if bsumm else None

diff_test_lines = 0
new_tests = 0
cur_is_test = False
for line in diff.splitlines():
    if line.startswith("+++ "):
        p = line[6:]
        cur_is_test = bool(re.search(r"(^|/)(test|tests|__tests__)/|\.(test|spec)\.[cm]?[jt]sx?$", p))
    elif cur_is_test and line.startswith("+") and not line.startswith("+++"):
        diff_test_lines += 1
        if re.match(r"^\+\s*(it|test)(\.each\([^)]*\))?\(", line):
            new_tests += 1


def ranges(ls, gap=2):
    out = []
    for l in sorted(ls):
        if out and l <= out[-1][1] + gap:
            out[-1][1] = l
        else:
            out.append([l, l])
    return out


def es(p, d=1):
    return f"{p:.{d}f}".replace(".", ",")


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- svg cards ----------------------------------------------------------------------------

THEMES = {
    "light": dict(bg="#ffffff", card="#f6f8fa", border="#d1d9e0", text="#1f2328", muted="#59636e", track="#e6eaef",
                  green="#1a7f37", yellow="#bf8700", red="#cf222e", faded="#f5b5b9", accent="#0969da", neutral="#8c959f"),
    "dark": dict(bg="#0d1117", card="#151b23", border="#3d444d", text="#f0f6fc", muted="#9198a1", track="#262c36",
                 green="#3fb950", yellow="#d29922", red="#f85149", faded="#5c2a2d", accent="#4493f8", neutral="#6e7681"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"


def col(t, p):
    return t["green"] if p >= args.threshold else t["yellow"] if p >= WARN else t["red"]


def ring(t, cx, cy, r, w, p, color):
    c = 2 * math.pi * r
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{t["track"]}" stroke-width="{w}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" stroke-width="{w}" stroke-linecap="round" '
            f'stroke-dasharray="{c * p / 100:.1f} {c:.1f}" transform="rotate(-90 {cx} {cy})"/>')


def svg_open(t, W, H):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">',
            f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12" fill="{t["bg"]}" stroke="{t["border"]}"/>']


def hero(t):
    W, H = 880, 250
    s = svg_open(t, W, H)
    s.append(f'<rect x="16" y="16" width="250" height="{H - 32}" rx="10" fill="{t["card"]}"/>')
    if newpct is None:
        s.append(ring(t, 141, 112, 62, 14, 0, t["track"]))
        s.append(f'<text x="141" y="120" text-anchor="middle" font-size="30" font-weight="700" fill="{t["muted"]}">—</text>')
        s.append(f'<text x="141" y="204" text-anchor="middle" font-size="13" font-weight="600" fill="{t["text"]}">Código nuevo de la PR</text>')
        s.append(f'<text x="141" y="222" text-anchor="middle" font-size="11.5" fill="{t["muted"]}">sin código ejecutable nuevo</text>')
    else:
        ok = newpct >= args.threshold
        s.append(ring(t, 141, 112, 62, 14, newpct, col(t, newpct)))
        s.append(f'<text x="141" y="112" text-anchor="middle" font-size="30" font-weight="700" fill="{t["text"]}">{es(newpct)}<tspan font-size="18">%</tspan></text>')
        s.append(f'<text x="141" y="134" text-anchor="middle" font-size="12" fill="{t["muted"]}">{N_cov} / {N_exec} líneas</text>')
        s.append(f'<text x="141" y="204" text-anchor="middle" font-size="13" font-weight="600" fill="{t["text"]}">Código nuevo de la PR</text>')
        s.append(f'<text x="141" y="222" text-anchor="middle" font-size="11.5" fill="{t["muted"]}">objetivo {es(args.threshold, 0)} % · '
                 f'<tspan fill="{t["green"] if ok else t["red"]}" font-weight="600">{"✓ cumple" if ok else "✗ por debajo"}</tspan></text>')
    # Project totals: neutral rings. The project's absolute level isn't something one PR can
    # fix; colour is kept for what the author controls (new code and the delta).
    s.append(f'<text x="290" y="40" font-size="13" font-weight="600" fill="{t["text"]}">Proyecto</text>'
             f'<text x="352" y="40" font-size="12" fill="{t["muted"]}">con esta PR</text>')
    for i, (k, name) in enumerate([("lines", "Lines"), ("statements", "Statements"), ("functions", "Functions"), ("branches", "Branches")]):
        p = T[k]["pct"]
        cx = 330 + i * 92
        s.append(ring(t, cx, 98, 32, 8, p, t["neutral"]))
        s.append(f'<text x="{cx}" y="103" text-anchor="middle" font-size="15" font-weight="700" fill="{t["text"]}">{es(p)}</text>')
        s.append(f'<text x="{cx}" y="152" text-anchor="middle" font-size="12" fill="{t["muted"]}">{name}</text>')
        if B:
            d = p - B[k]["pct"]
            a, dc = ("▲", t["green"]) if d >= 0.05 else ("▼", t["red"]) if d <= -0.05 else ("=", t["muted"])
            s.append(f'<text x="{cx}" y="171" text-anchor="middle" font-size="12" font-weight="600" fill="{dc}">{a} {es(abs(d))}</text>')
    s.append(f'<text x="290" y="222" font-size="11.5" fill="{t["muted"]}">'
             f'{"Δ frente a la base de la PR · " if B else ""}% de cobertura</text>')
    x = 668
    s.append(f'<text x="{x}" y="40" font-size="13" font-weight="600" fill="{t["text"]}">Esta PR</text>')
    rows = [(f"{len(files)}", "ficheros con código tocado"), (f"+{N_exec}", "líneas ejecutables nuevas"),
            (f"+{new_tests}", "tests nuevos"), (f"+{diff_test_lines}", "líneas de test")]
    for i, (v, lab) in enumerate(rows):
        y = 78 + i * 38
        s.append(f'<text x="{x}" y="{y}" font-size="20" font-weight="700" fill="{t["accent"]}">{v}</text>')
        s.append(f'<text x="{x}" y="{y + 16}" font-size="11.5" fill="{t["muted"]}">{lab}</text>')
    s.append("</svg>")
    return "\n".join(s)


def files_card(t, fs):
    W, row = 880, 64
    H = 56 + row * len(fs) + 34
    s = svg_open(t, W, H)
    s += [f'<text x="20" y="34" font-size="13" font-weight="600" fill="{t["text"]}">Ficheros de la PR</text>',
          f'<text x="318" y="34" font-size="12" fill="{t["muted"]}">Código nuevo cubierto</text>',
          f'<text x="560" y="34" font-size="12" fill="{t["muted"]}">Mapa del fichero</text>']
    bx, bw, mx, mw = 318, 200, 560, 300
    for i, x in enumerate(fs):
        y = 56 + i * row
        p = x["pct"]
        if i:
            s.append(f'<line x1="16" y1="{y - 6}" x2="{W - 16}" y2="{y - 6}" stroke="{t["border"]}" opacity="0.6"/>')
        name = x["name"] if len(x["name"]) <= 30 else x["name"][:29] + "…"
        s.append(f'<text x="20" y="{y + 20}" font-size="14" font-weight="600" fill="{t["text"]}">{esc(name)}</text>')
        if x["new"]:
            nx = 20 + len(name) * 7.9 + 8
            s.append(f'<rect x="{nx:.0f}" y="{y + 7}" width="44" height="18" rx="9" fill="none" stroke="{t["accent"]}"/>'
                     f'<text x="{nx + 22:.0f}" y="{y + 20}" text-anchor="middle" font-size="10.5" font-weight="600" fill="{t["accent"]}">nuevo</text>')
        d = x["dir"] if len(x["dir"]) <= 38 else "…" + x["dir"][-37:]
        s.append(f'<text x="20" y="{y + 40}" font-size="11.5" font-family="{MONO}" fill="{t["muted"]}">{esc(d)}</text>')
        s.append(f'<rect x="{bx}" y="{y + 10}" width="{bw}" height="10" rx="5" fill="{t["track"]}"/>')
        if p > 0:
            s.append(f'<rect x="{bx}" y="{y + 10}" width="{max(10, bw * p / 100):.1f}" height="10" rx="5" fill="{col(t, p)}"/>')
        tx = bx + bw * args.threshold / 100
        s.append(f'<line x1="{tx:.1f}" y1="{y + 6}" x2="{tx:.1f}" y2="{y + 24}" stroke="{t["text"]}" stroke-width="1.5" opacity="0.55"/>')
        s.append(f'<text x="{bx}" y="{y + 41}" font-size="12" fill="{t["muted"]}"><tspan font-size="14" font-weight="700" fill="{col(t, p)}">{es(p)} %</tspan>'
                 f'   {x["covered"]}/{x["executable"]} · fichero {es(x["lines"])} %</text>')
        s.append(f'<rect x="{mx}" y="{y + 8}" width="{mw}" height="16" rx="3" fill="{t["track"]}"/>')

        def band(a, b, color, yy, hh):
            s.append(f'<rect x="{mx + (a - 1) / x["total"] * mw:.1f}" y="{yy}" width="{max(2, (b - a + 1) / x["total"] * mw):.1f}" height="{hh}" fill="{color}"/>')

        for a, b in ranges(x["added"], gap=0):
            band(a, b, t["accent"], y + 8, 4)
        for a, b in ranges(set(x["uncovered_all"]) - set(x["uncovered_new"]), gap=0):
            band(a, b, t["faded"], y + 12, 12)
        for a, b in ranges(x["uncovered_new"], gap=0):
            band(a, b, t["red"], y + 12, 12)
        s.append(f'<text x="{mx}" y="{y + 41}" font-size="11.5" font-family="{MONO}" fill="{t["muted"]}">L1</text>'
                 f'<text x="{mx + mw}" y="{y + 41}" text-anchor="end" font-size="11.5" font-family="{MONO}" fill="{t["muted"]}">L{x["total"]}</text>')
    s.append(f'<text x="20" y="{H - 14}" font-size="11.5" fill="{t["muted"]}">│ objetivo del {es(args.threshold, 0)} % · de peor a mejor</text>')
    s.append(f'<text x="{W - 20}" y="{H - 14}" text-anchor="end" font-size="11.5" fill="{t["muted"]}"><tspan fill="{t["accent"]}">▬</tspan> líneas nuevas   '
             f'<tspan fill="{t["red"]}">▮</tspan> nuevas sin test   <tspan fill="{t["faded"]}">▮</tspan> antiguas sin test</text>')
    s.append("</svg>")
    return "\n".join(s)


for theme, t in THEMES.items():
    open(f"{args.out}/hero-{theme}.svg", "w").write(hero(t))
    open(f"{args.out}/files-{theme}.svg", "w").write(files_card(t, files[: args.max_files]))

# --- markdown --------------------------------------------------------------------------------

blob = f"https://github.com/{args.repository}/blob/{args.sha}" if args.repository and args.sha else ""


def link(path, a=None, b=None):
    if not blob:
        return None
    anchor = "" if a is None else f"#L{a}" if a == b else f"#L{a}-L{b}"
    return f"{blob}/{path}{anchor}"


def code(path):
    u = link(path)
    return f"[`{path}`]({u})" if u else f"`{path}`"


def pct(p):
    return f"{es(p)}{NBSP}%"


def picture(name, alt):
    u = args.images_url.rstrip("/")
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="{u}/{name}-dark.svg">\n'
            f'  <img alt="{alt}" src="{u}/{name}-light.svg" width="100%">\n</picture>')


def delta_txt(d, new=False):
    if d is None:
        return "nuevo" if new else "—"
    return "=" if abs(d) < 0.05 else f"{'+' if d > 0 else '−'}{es(abs(d))}"


title = f"🧪 Coverage{f' · {args.title}' if args.title else ''}"

alert = []
low = [x for x in files if x["executable"] >= 5 and x["pct"] < WARN]
if low:
    alert.append("> [!WARNING]")
    for x in low[:3]:
        fns = f": sin test en {', '.join(f'`{n}`' for n in x['untested_fns'][:3])}" if x["untested_fns"] else ""
        alert.append(f"> **`{x['name']}`**: {len(x['uncovered_new'])} de {x['executable']} líneas nuevas sin cubrir{fns}.  ")
elif newpct is not None and newpct >= args.threshold:
    alert += ["> [!TIP]", f"> El código nuevo está cubierto al **{pct(newpct)}**. ¡Buen trabajo!"]

snippets = []
with_gaps = [x for x in files if x["uncovered_new"]]
if with_gaps:
    total_gaps = sum(len(x["uncovered_new"]) for x in with_gaps)
    snippets += ["<details>",
                 f"<summary><b>🔍 Líneas nuevas sin test</b> · {total_gaps} líneas en {len(with_gaps)} "
                 f"fichero{'s' if len(with_gaps) != 1 else ''}</summary>", "",
                 "<sub>Las líneas marcadas con <code>!</code> son nuevas en esta PR y no las ejecuta ningún test.</sub>", ""]
    for x in with_gaps[: args.max_snippets]:
        rs = ranges(x["uncovered_new"])
        refs = []
        for a, b in rs[:4]:
            label = f"L{a}" if a == b else f"L{a}–{b}"
            u = link(x["path"], a, b)
            refs.append(f"[{label}]({u})" if u else label)
        more = f" · +{len(rs) - 4} más" if len(rs) > 4 else ""
        snippets += [f"**{code(x['path'])}** · {' · '.join(refs)}{more}", "```diff"]
        a, b = rs[0]
        lo, hi = max(1, a - 1), min(len(x["src"]), min(b, a + 9) + 1)
        gaps = set(x["uncovered_new"])
        for n in range(lo, hi + 1):
            snippets.append(f"{'!' if n in gaps else ' '}{n:>4} │ {x['src'][n - 1][:110]}")
        snippets += ["```", ""]
    if len(with_gaps) > args.max_snippets:
        snippets += [f"<sub>… y {len(with_gaps) - args.max_snippets} ficheros más en la tabla de abajo.</sub>", ""]
    snippets += ["</details>", ""]


def files_table(open_):
    rows = ["| Fichero | Código nuevo | Lines | Branches | Funcs | Δ Lines |", "|---|---:|---:|---:|---:|---:|"]
    for x in files:
        rows.append(f"| {code(x['path'])} | **{pct(x['pct'])}** <sub>{x['covered']}/{x['executable']}</sub> | "
                    f"{pct(x['lines'])} | {pct(x['branches'])} | {pct(x['funcs'])} | {delta_txt(x['delta'], x['new'])} |")
    head = f"<details{' open' if open_ else ''}>\n<summary><b>📊 Ficheros de la PR</b> · {len(files)}</summary>\n"
    return [head, *rows, "", "</details>", ""]


footer_bits = [b for b in (args.runner, f"commit [`{args.sha[:7]}`](https://github.com/{args.repository}/commit/{args.sha})"
                           if args.repository and args.sha else "",
                           f"[Informe HTML completo ↗]({args.report_url})" if args.report_url else "",
                           "Solo informativo, no bloquea el merge", "se actualiza en cada push") if b]
footer = f"<sub>{' · '.join(footer_bits)}</sub>"
marker = f"<!-- {args.marker} -->"

if not files:
    headline_plain = [f"### {title}", "", "Esta PR no cambia código ejecutable que midan los tests.", "",
                      f"Proyecto: **{pct(T['lines']['pct'])}** de líneas cubiertas."]
    for name in ("comment-cards.md", "comment-plain.md"):
        open(f"{args.out}/{name}", "w").write("\n".join([marker, *headline_plain, "", footer]) + "\n")
else:
    cards = [marker, picture("hero", f"Coverage: código nuevo {es(newpct)} %, proyecto {es(T['lines']['pct'])} %"), "",
             *alert, "", picture("files", "Cobertura del código nuevo por fichero"), "",
             *snippets, *files_table(False), footer]
    d = lambda k: f" <sub>({delta_txt(T[k]['pct'] - B[k]['pct'])})</sub>" if B else ""
    plain = [marker, f"### {title}", "",
             f"**Código nuevo de la PR: {pct(newpct)}** · {N_cov} de {N_exec} líneas ejecutables tienen test "
             f"<sub>(objetivo {es(args.threshold, 0)}{NBSP}%)</sub>", "",
             "| Lines | Statements | Functions | Branches |", "|:---:|:---:|:---:|:---:|",
             f"| {pct(T['lines']['pct'])}{d('lines')} | {pct(T['statements']['pct'])}{d('statements')} | "
             f"{pct(T['functions']['pct'])}{d('functions')} | {pct(T['branches']['pct'])}{d('branches')} |", "",
             *alert, "", *files_table(True), *snippets, footer]
    open(f"{args.out}/comment-cards.md", "w").write("\n".join(cards) + "\n")
    open(f"{args.out}/comment-plain.md", "w").write("\n".join(plain) + "\n")

summary = {"new_code_pct": None if newpct is None else round(newpct, 2), "new_lines": N_exec, "new_lines_covered": N_cov,
           "project_lines_pct": T["lines"]["pct"], "files": len(files)}
json.dump(summary, open(f"{args.out}/summary.json", "w"))
print(json.dumps(summary))
