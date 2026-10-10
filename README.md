# Footnote

Incident RAG for on-call engineers: paste an error, get similar past Kubernetes incidents,
the fix that resolved them and the docs/release notes for your version — every claim cited,
"I don't know" when the evidence is weak.

_Status: Phase 0 — setup & corpus._

## Corpus (Phase 0)

Output of `python scripts/corpus_stats.py --markdown`:

_Pending: issue fetch needs GITHUB_TOKEN._

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
