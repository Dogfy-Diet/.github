# coverage-report

Composite action that publishes the coverage of a PR as a comment: how much of the **new code** has tests, the files the PR touches, a map of where the untested lines are, and the exact new lines no test runs, with links to them.

It is informative only: it never fails the job, and it never blocks a merge.

## What it shows

1. **Header card**: coverage of the PR's new executable lines against the target (80 % by default), the project's lines / statements / functions / branches, and the PR in numbers (files, new lines, new tests).
2. **Alert**: files where less than half of the new code has tests, naming the functions with no test at all.
3. **Files card**: one row per file, worst first, with the new-code bar and a map of the file (new lines, new lines without tests, old lines without tests).
4. **New lines without tests**: the code itself, marked with `!`, linked to the lines in GitHub.
5. **Table** of every file in the PR, plus a link to the full HTML report.

The comment is edited on every push (it is found by a hidden marker), so a PR has exactly one. The same content goes to the job summary.

## Usage

In the job that runs the tests, on `pull_request`:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: write        # the SVG cards are committed to the coverage-assets branch
      pull-requests: write   # the PR comment
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0     # the report diffs the PR against its base

      # … setup and install …

      - name: Tests with coverage
        run: >
          npx vitest run --coverage
          --coverage.reporter=json-summary --coverage.reporter=json
          --coverage.reporter=lcov --coverage.reporter=html

      - name: Upload the HTML report
        id: html
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-html
          path: coverage/

      - name: Coverage report
        if: always()
        uses: Dogfy-Diet/.github/actions/coverage-report@v1.7.0
        with:
          title: api
          runner: Vitest · v8
          report-url: ${{ steps.html.outputs.artifact-url }}
```

Jest works the same way: `jest --coverage --coverageReporters=json-summary --coverageReporters=json --coverageReporters=lcov`.

| Input | Default | |
|---|---|---|
| `coverage-dir` | `coverage` | dir with `coverage-summary.json`, `coverage-final.json`, `lcov.info` |
| `title` | — | name in the header |
| `threshold` | `80` | target for the new code (colours and the alert only) |
| `base-summary` | — | `coverage-summary.json` of the base branch, to show the project deltas |
| `report-url` | — | link to the full HTML report |
| `runner` | — | label for the footer |
| `comment` | `true` | `false`: job summary only |
| `cards` | `true` | `false`: plain markdown, no images, no `contents: write` needed |
| `assets-branch` | `coverage-assets` | orphan branch for the cards |
| `marker` | `coverage-report` | change it to keep several reports in one PR (monorepos) |
| `github-token` | `github.token` | |

Outputs: `new-code-pct`, `project-lines-pct`.

## How it works

- `report.py` (Python standard library only) reads the Istanbul output, diffs the checkout against `merge-base(PR base, HEAD)` and keeps the lines the PR adds or changes. "New code" counts only executable lines: comments, types and blank lines don't count.
- The SVG cards are committed to an orphan branch of the calling repo (`coverage-assets/pr-<n>/<sha>/`) and referenced through `github.com/<repo>/raw/…`, which GitHub serves to anyone who can read the repo, private repos included. The light or dark card is chosen with `prefers-color-scheme`.
- Without write access (Dependabot, forks) it degrades to plain markdown and the job summary, with a warning.

## Development

```bash
bash actions/coverage-report/test/run.sh
```

The fixture in `test/fixture` is a toy project with real Vitest v8 output; this repo is public, so it must never contain code from our services.
