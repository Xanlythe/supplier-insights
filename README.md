# Supplier Insights

A personal, **non-commercial** prototype for understanding corporate supply
chains: parent/subsidiary relationships, location evidence, and attributed
government, PEP, and debarment connections. Not a financial tool or a compliance
clearance service.

The [project knowledge capture](docs/PROJECT_KNOWLEDGE.md) records the architecture,
implementation decisions, Microsoft walkthrough lessons, licensing research,
validation evidence, and known gaps from the kickoff session.
Source terms and attribution are maintained in [DATA_SOURCES.md](DATA_SOURCES.md).

## Setup

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```powershell
uv sync --locked --python 3.12
Copy-Item .env.example .env
uv run supplier-insights --help
```

`.env` is optional and ignored by Git. Set `SUPPLIER_INSIGHTS_USER_AGENT` to a
descriptive identifier with your project URL or contact information.
`SUPPLIER_INSIGHTS_DATA_DIR` defaults to `.supplier-insights` in the current
directory. It holds a response cache, a local screening database, and the last
lookup's graph in `graph.sqlite`; keep it private. Each lookup atomically replaces
that graph. Redirect JSON output to retain separate reports.
No API keys are needed for the implemented interfaces.

FtM 3.8.2, rigour 0.9.3, and countrynames below 1.17 are constrained to install
without native ICU on Windows. The lockfile fixes a compatible dependency set.
Upgrading FtM requires revisiting ICU installation and checking schema compatibility.

## Usage

```powershell
# Explicitly download and index the OpenSanctions default collection.
# This may be a large download. Nothing is downloaded by lookup.
uv run supplier-insights sync

# Or import an existing newline-delimited FtM snapshot.
uv run supplier-insights import-ftm .\snapshot.ftm.json --dataset default

# Readable tree on stderr; JSON on stdout.
uv run supplier-insights lookup "Siemens" --depth 2 > lookup.json

# Resolve ambiguous search results using identifiers from the error/candidate list.
uv run supplier-insights lookup "Siemens" --qid Q81230 --lei W38RGI023J3WT1HWRP32

# Machine output only; do not print the tree.
uv run supplier-insights lookup "Siemens" --qid Q81230 --lei W38RGI023J3WT1HWRP32 --no-tree
```

Wikidata name search filters candidates to organizations, excluding product-only
matches such as Windows or Excel. A sole exact company-name match is selected
automatically (ignoring case and extra spaces), even if broader name matches also
exist. This is name-based selection within a source, not a cross-source identity
merge. If multiple organizations share the exact name, or there are multiple
broader matches and no exact match, lookup stops with an organization candidate
list, not a subsidiary tree. `--qid` and `--lei` explicitly select source records.
Supplying both without a shared identifier keeps them separate and reports the
identity uncertainty. A name match alone never merges records.

For example:

```powershell
.\.venv\Scripts\supplier-insights.exe lookup "Microsoft" > lookup.json
```

The terminal shows the company tree and evidence, while `lookup.json` receives
the machine-readable report. This selects Microsoft's company record, not its
products; subsidiary records include LinkedIn Corporation, GitHub, and ZeniMax
Media where documented in the source snapshot and within traversal limits.

`--depth` (default 1, range 0-5) controls independent upstream and downstream
walks from the selected root(s); ancestor siblings are not recursively expanded.
GLEIF's reported ultimate parent is also shown as a distinct shortcut when
depth is at least 1, not as a fabricated direct parent. `--max-nodes` (default
100) bounds the graph; `--relation-limit` (default 100) bounds each source's
one-hop results. Reverse Wikidata statements are bounded before organization
filtering, so this is not a guaranteed count of companies. Limits and partial
source failures appear in `warnings`.
Depth 0 fetches only the selected companies. JSON includes FtM entities,
source-labeled edges, facts, screening results, and snapshot provenance.

The local screening index uses exact LEI/QID matches and normalized exact name
candidates, plus one-hop FtM relationship context (including ownership and
positions). This is not a fuzzy matching engine. Name-only hits and connections
are explicitly unverified. It preserves Person, Position, Address, and other FtM
records as evidence, without assuming that any associated person is a PEP.
Without an imported snapshot, screening is **not performed** and is labeled so.

Headquarters and registration countries are reported separately from Wikidata's
explicit operating areas (P2541). Only operating areas with country codes are
listed as operating countries. Unknown coverage is never filled in using HQ or
subsidiary addresses. Corporate trees and government ties are necessarily partial.
Wikidata class hierarchies identify organizations and SOEs; generic ownership
objects (for example, treasury stock) remain `Thing` evidence, not invented companies.
Reverse subsidiary discovery accepts organization targets, not software or patents
that merely have an "owned by" statement. Explicit P355 subsidiary assertions are
preserved even if a company's classification is missing.
Review the original sources and [data licenses](DATA_SOURCES.md) before reuse.

## Usage troubleshooting

- Run the executable, subcommand, and name together, for example
  `.\.venv\Scripts\supplier-insights.exe lookup "Microsoft"`.
  `lookup` is not a standalone PowerShell command; omitting the name causes a
  missing-argument error.
- A first lookup of a large company can take several minutes. Requests are
  rate-limited, and Wikidata queries can be slow. The current CLI prints its
  tree only after collection finishes; it has no progress indicator.
  Cached repeats should be faster.
- `> lookup.json` keeps machine-readable JSON out of the terminal while the
  readable tree and warnings remain on stderr.
- A search candidate list is not the subsidiary tree. If multiple organizations
  genuinely match, select one with the identifiers shown by `--qid` or `--lei`.
- OpenSanctions `not_performed` means no snapshot is imported in the configured
  data directory. It does not mean that no relevant connections exist.

## Development

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Tests mock all external APIs and use synthetic screening records. CI uses
Python 3.12 and the committed uv lockfile.

```text
src/supplier_insights/
  cli.py            CLI, configuration, JSON and tree output
  http.py           SQLite response cache, throttling, retries
  models.py         Evidence records, identifiers, FtM mapping
  sources/          Wikidata and GLEIF adapters
  graph.py          Identity resolution and bounded graph traversal
  screening.py      Atomic local FtM imports and evidence screening
tests/              Mocked HTTP and synthetic FtM tests
```

Optional graph visualization and OpenAleph evaluation are deferred. No paid
APIs, legacy Aleph deployment, factory data, or filing parsers are included.
