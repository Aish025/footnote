# Footnote

Product evidence intelligence: answers shopper questions ("Is this case waterproof?",
"Will this charger work with a Pixel 8?") from listing claims, customer reviews and
community Q&A — every claim cited, conflicts surfaced, counts computed by SQL, and an
explicit abstention when evidence is insufficient.

_Status: Phase 0 (setup & corpus) complete. Next: Phase 1 — human-written golden set._

## Corpus (v1)

Source: [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/) (McAuley Lab, UCSD),
category Cell_Phones_and_Accessories, plus the older McAuley Amazon Q&A datasets
(2016 and 2019) where ASINs overlap. Files, sizes and checksums: `data/manifest.yaml`.

v1 subset rule ([D-010](DECISIONS.md)): four accessory buckets (chargers, cables, cases,
single-ear Bluetooth headsets) by category path; listing must have a title and
features or description; 50–2,000 reviews; up to 1,000 products per bucket in a stable
hash order. Every selected product keeps **all** of its reviews, so SQL counts are
complete for the stored data.

Figures below are produced by `python scripts/corpus_stats.py --markdown` — every number
is a SQL query over the loaded Postgres tables.

| Measure | Value |
|---|---:|
| Products | 2,816 (cables 584, cases 1,000, chargers 1,000, headsets 232) |
| Reviews | 623,218 |
| Review date range | 2004-12-13 to 2023-09-11 |
| Verified purchase | 94.4% |
| Reviews per product (min / median / p90 / max) | 46 / 116 / 515.5 / 1,975 |
| Q&A pairs | 2,371 (59 products) |
| Estimated chunks | 639,850 (listing 6,827, review 630,652, qa 2,371) |

Noise visible already: 11.4% of reviews are under 20 characters; 514 user/product pairs
have more than one review; 6,981 exact-duplicate review records (1.1%) were dropped at
load time, which is why the minimum is 46 rather than the selection floor of 50;
290 Q&A pairs appear in both Q&A datasets. Only 2.1% of products have Q&A ([D-014](DECISIONS.md)).

## Data licence

The Amazon Reviews 2023 and Amazon Q&A datasets are released by the McAuley Lab for
**research use**; the dataset authors do not own the underlying review and listing text.
This project is a non-commercial portfolio/research project and must not be used
commercially. Raw and processed data are never committed to this repository — the
scripts below rebuild them from the original source.

## Reproduce the corpus

Requires Python 3.11+ and Docker.

```bash
pip install -e ".[dev]"
cp .env.example .env                  # local Postgres settings; no API keys needed
docker compose up -d                  # Postgres 17 + pgvector

python scripts/download_data.py       # ~3.4 GB, resumable, sha256 recorded
python scripts/select_subset.py       # v1 subset -> data/processed/
python scripts/check_qa_overlap.py    # Q&A overlap -> data/processed/qa_overlap.json
python scripts/load_db.py             # idempotent upsert into Postgres
python scripts/corpus_stats.py        # the table above
pytest && ruff check .
```

Design decisions and their trade-offs are logged in [DECISIONS.md](DECISIONS.md).
