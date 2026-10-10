# Footnote

Incident RAG for on-call engineers: paste an error, get similar past Kubernetes incidents,
the fix that resolved them and the docs/release notes for your version — every claim cited,
"I don't know" when the evidence is weak.

_Status: Phase 0 — setup & corpus._

## Corpus (Phase 0)

Output of `python scripts/corpus_stats.py --markdown`:

| Source | Count | Notes |
|---|---:|---|
| Closed issues with merged fix PR | 2,799 | closed 2022-01-03 to 2026-09-30 |
| Unique fix PRs | 2,945 | 2,774 with a release milestone |
| Issue comments | 27,463 | median 6/issue; 3,817 from bots |
| Release-note entries | 3,327 | CHANGELOG 1.33, 1.34, 1.35, 1.36, 1.37 |
| Docs pages | 7,989 | 5 versions (1.33, 1.34, 1.35, 1.36, 1.37) |

Known gaps (fetched 2026-10-10):
- **Below the ~5K target.** Every closed issue since 2022-01-01 that GitHub links to a merged
  PR is included; there are only 2,799. 800 more closed issues had a linked PR that was never merged.
- **Version coverage.** Fix PRs ship in v1.24–v1.38, but docs and release notes cover 1.33–1.37,
  so only 924 of 2,799 issues (33%) can follow issue → fix → release → docs today.

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env              # add GITHUB_TOKEN (public repo read access)
docker compose up -d              # Postgres 17 + pgvector
python scripts/fetch_docs.py      # release notes + versioned docs (no token needed)
python scripts/fetch_issues.py    # ~5K closed issues with merged fix PRs (resumable)
python scripts/corpus_stats.py
pytest
```

Raw data lands in `data/raw/` (gitignored). `data/manifest.yaml` defines exactly what is fetched.
