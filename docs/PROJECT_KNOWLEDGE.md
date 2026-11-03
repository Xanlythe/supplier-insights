# Supplier Insights: project knowledge

Captured from the project kickoff and working session on November 2, 2026.
This is the implementation and research handoff, not a promise of complete data
coverage or legal advice. Licensing findings below describe the linked documents
as reviewed during that session; check current terms before changing use.

## 1. Purpose and boundaries

Supplier Insights is a personal, non-commercial prototype for understanding
corporate supply chains. The questions are:

- Who owns a company, which organizations sit above it, and which subsidiaries
  and related companies sit below it?
- Where is its headquarters, and where does available evidence say it operates?
- What documented state ownership, SOE, PEP, or international-body connections
  are associated with it?

This is not a financial-market tool, a complete company registry, a legal
determination about sanctions, or a compliance clearance service. Neither
political exposure nor state ownership implies wrongdoing.

## 2. What is implemented

| Surface | Current behavior |
|---|---|
| `lookup` | Resolves a company through Wikidata and GLEIF, walks a bounded graph, reports location evidence, and screens against locally imported OpenSanctions snapshots. |
| `sync` | Explicitly downloads an OpenSanctions collection and replaces that collection in the local screening index. The default collection is `default`; downloads can be large. |
| `import-ftm` | Imports a local newline-delimited FtM file without network access. |
| Output | Machine-readable JSON on stdout; readable tree, facts, screening information, and warnings on stderr. |
| Persistence | SQLite response cache, last-lookup corporate graph, and local screening index. |
| Development | uv lockfile, Python 3.12 environment, mocked pytest suite, Ruff, Hatchling packaging, and a GitHub Actions workflow. |

Graph visualization is not implemented. There is no OpenAleph deployment, hosted
OpenSanctions API integration, or external database service.

## 3. Software choices and the Windows lesson

| Component | Role | License / decision |
|---|---|---|
| Python 3.12 | Application runtime | PSF; `.python-version` selects 3.12. |
| FollowTheMoney (FtM) 3.8.2 | Entity schemas and normalization | MIT; no paid commercial software license is required. |
| rigour 0.9.3 | FtM normalization dependency | MIT; pinned for the portable dependency set. |
| countrynames below 1.17 | FtM country handling dependency | MIT; constrained to avoid mandatory native ICU. |
| SQLite | Local cache, graph storage, and screening index | Public domain; Python's standard-library interface is used. |
| HTTPX | HTTP requests and streamed downloads | BSD-3-Clause. |
| Typer | CLI commands, arguments, and help | MIT. |
| python-dotenv | Optional local configuration | BSD-3-Clause. |
| tzdata | Time-zone database on Windows | Apache-2.0 package; explicit Windows dependency. |
| uv, pytest, Ruff, Hatchling | Dependency management, tests, lint/format, and packaging | Open-source development tools; package versions are recorded in the lockfile where applicable. |

The initial attempt to install newer FtM releases failed because transitive
dependencies required PyICU, which in turn required unavailable native ICU
tooling on the Windows execution host. Pinning FtM alone was insufficient:
rigour and countrynames also needed compatible constraints. The working set is
recorded in `pyproject.toml` and `uv.lock`.

The Windows Python runtime also needed `tzdata` for the dependencies' use of
`ZoneInfo("UTC")`. Adding a Windows-specific dependency resolved the import failure.

These are compatibility decisions, not licensing restrictions. Upgrade FtM only
after revisiting ICU support, schema compatibility, and the complete dependency
chain. The successfully imported World Bank export does not establish that
every current OpenSanctions collection is compatible with this older FtM model.

NetworkX is installed transitively through FtM, but it is not our graph engine.
Traversal uses the Python graph module, and the resulting graph is stored in
SQLite. No license for this repository's own source code was selected during
this session; dependency licenses do not automatically license this project.

## 4. Data sources are not software dependencies

We call Wikidata and GLEIF over HTTP; they do not appear as Python packages in
the dependency manifest. OpenSanctions contributes an explicitly imported local
dataset. FtM is the software model used to represent entities.

| Source | Useful evidence | Important limitations |
|---|---|---|
| Wikidata | Parent organizations, subsidiaries, ownership assertions, identifiers, headquarters, country associations, operating areas, and organization/SOE classifications. | Community-maintained, incomplete, sometimes inconsistent or historical. Its name search includes non-company subjects. |
| GLEIF | LEIs, legal/HQ addresses, reported direct and ultimate parents, and direct children. | Parent relationships represent accounting consolidation, not ownership percentages. LEI coverage and parent reporting are incomplete. |
| OpenSanctions | Source-attributed sanctions, PEP, state-ownership/SOE, debarment, and relationship context where covered by the selected collection. | Not a universal ownership registry. Coverage, snapshot age, matching ambiguity, and source dates affect findings. |

The source-specific attribution and usage register is
[`DATA_SOURCES.md`](../DATA_SOURCES.md). Keep it updated when adding a source.

## 5. Identity resolution: selection is not merging

There are two different decisions:

1. **Selecting a source record.** Wikidata name-search candidates are filtered
   using organization classifications. A sole exact name match is selected
   automatically, ignoring case and extra spaces. A sole candidate can also be
   selected. Multiple exact matches, or multiple broader candidates with no exact
   match, require an explicit `--qid` or `--lei`.
2. **Joining records across sources.** A shared LEI or Wikidata QID is required.
   Records with conflicting known identifiers remain separate and produce a
   warning. Name equality alone never causes a cross-source merge.

The exact-name selection behavior was explicitly chosen during the session after
the original CLI stopped on Microsoft's broad search results.

Wikidata P1278 supplies LEIs. The graph can enrich Wikidata records from GLEIF
using those LEIs, and can look up Wikidata QIDs for GLEIF records. Ambiguous
reverse LEI joins are not silently resolved. Additional source labels are
preserved as aliases when records merge.

In the local OpenSanctions index, matches are labeled `identifier`,
`unverified_name`, or `identifier_conflict`. Name matching uses Unicode NFKC
normalization, case folding, punctuation cleanup, and whitespace normalization;
it is not fuzzy matching. Identifier candidates are prioritized over name-only
candidates. Screening considers the preserved aliases as well as the main name.

## 6. Wikidata relationships and the products-versus-companies lesson

| Property | Meaning in this application |
|---|---|
| P749 | Parent organization |
| P355 | Subsidiary |
| P127 | Owned by |
| P1278 | LEI |
| P159 | Headquarters location |
| P17 | Associated country; not automatically a country of operation |
| P2541 | Operating area; a country code is required before reporting it as an operating country |
| P31 / P279 | Instance/subclass classification, including organization and SOE identification |
| Q270791 | State-owned enterprise class |

Two related problems appeared during the Microsoft walkthrough:

- **Search results were mistaken for subsidiary results.** Raw name search
  returned Microsoft alongside Windows, Excel, and other products. Lookup had
  stopped at disambiguation; it had not generated a corporate tree. Search now
  filters organization candidates, selects the sole exact name match, and makes
  genuine ambiguity errors explicit about the missing tree.
- **Owned assets were mistaken for subsidiary companies.** P127 also applies to
  software, patents, and other non-company assets. Reverse subsidiary discovery
  now filters targets to organizations rather than putting every owned object
  into the company tree.

Explicit P355 subsidiary assertions are retained even if the target's
classification is missing. Such a target can remain a `Thing` in FtM output;
it is not silently relabeled as a company. Parent-side ownership evidence can
also include non-company owners or objects. Source assertions are evidence,
not proof that every edge is a legally direct parent/subsidiary relationship.

Relationship traversal is one hop at a time. Deprecated statements and
statements with an elapsed end-date qualifier are excluded where the adapter
handles them. This is not a complete historical reconstruction.

Reverse statements catch relationships recorded on only one side. If a reverse
query or its organization filtering fails, available explicit relationships are
retained and a warning explains the missing reverse coverage.

Classification queries bind bounded lists of entity IDs. A broader exploratory
Microsoft reverse-relationship query with a global organization hierarchy
filter timed out; do not assume combining a global class traversal with an
ownership query will be efficient.

## 7. Graph semantics and bounds

The graph retains distinct edge kinds:

- `parent_organization`: organizational parent/subsidiary assertion.
- `ownership`: explicit ownership assertion; no ownership percentage is invented.
- `accounting_direct_parent`: GLEIF accounting-consolidation parent.
- `accounting_ultimate_parent`: GLEIF's separately reported ultimate parent,
  represented as a shortcut rather than a fabricated direct-parent edge.

Only explicit `ownership` edges are emitted as FtM `Ownership` entities.
Organizational and accounting links remain labeled graph edges rather than
being falsely converted into percentage ownership.

Default CLI limits are depth 1, 100 nodes, and a relation limit of 100. Depth is
configurable from 0 to 5; CLI node and relation limits are configurable from 1
to 1,000. Depth 0 does not traverse corporate relationships, but still fetches
selected-record evidence and can join source identities.

Upstream and downstream walks start independently at the selected root(s).
The app does not recursively expand an ancestor's other children. Cycles and
shared descendants are handled; repeated tree branches are marked as references.
Cross-source enrichment does not consume an extra corporate relationship level.
If a shorter path is found through another source, the node can be revisited
at that shallower level.

Reverse Wikidata statements are bounded **before** organization filtering.
Consequently, a relation limit of 100 does not promise 100 company results, and
non-company records can consume part of that source-query window. Warnings must
remain visible when interpreting completeness. GLEIF lists are paginated within
the configured bounds; reporting exceptions are surfaced rather than treated
as proof that no parent exists.

## 8. Location evidence

The output distinguishes headquarters, legal addresses, registration/jurisdiction
information, associated countries, operating areas, and operating countries.

A headquarters address in one country does not establish operations elsewhere.
A subsidiary address does not automatically establish its parent's operating
countries. P2541 areas without country codes remain areas, not invented country
lists. Missing operating-country evidence is reported as unknown/not reported.

FtM Address entities preserve address evidence. A company with no operating
countries in this output may simply lack usable source data.

## 9. What OpenSanctions is, and is not

OpenSanctions aggregates public records from many sources, standardizes them,
and connects records referring to the same entities. It is not a government
agency and does not itself impose sanctions.

| Category | Interpretation |
|---|---|
| Sanctions | An authority lists an entity under a sanctions program; review the authority, program, dates, and applicable law. |
| PEP | A person holds or held a significant public role; this is not an allegation of misconduct. |
| State ownership / SOE | Available source evidence identifies government ownership or state-owned enterprise status; coverage is not universal. |
| Debarment | Exclusion from certain procurement contracts, such as World Bank-financed contracts. |
| Related entity | A documented relationship to another entity; not automatically the same legal status as that entity. |

Being present in OpenSanctions does not mean an entity is sanctioned. A
sanction-related connection does not by itself establish a legal prohibition.
Review the original records and dates instead of inferring guilt or clearance.

Our implementation is a limited local index, not OpenSanctions' full hosted
matching service. It reports selected topics and one-hop relationship context,
including relationship records and referenced endpoints. It does not recursively
trace every possible political connection or implement jurisdiction-specific
sanctions ownership/control rules.

Screening returns at most 50 matches per node and 100 connections per match,
with truncation indicators. These bounds are not evidence of full coverage.
Without an imported snapshot, the status is `not_performed`, not a clean result.

## 10. Snapshot acquisition and integrity

`lookup` never automatically downloads OpenSanctions bulk files. That behavior
was explicitly chosen so a company lookup cannot unexpectedly trigger a large
download.

`sync` defaults to the `default` collection, which OpenSanctions recommends for
connected and enriched records. A narrower collection is possible with
`--dataset`, but changes coverage. Relevant catalog areas include PEPs, EITI
SOEs, World Bank debarment, and GEM energy ownership; their presence must be
checked in the actual collection rather than assumed.

The sync flow is:

1. Fetch fresh collection metadata, bypassing the normal response-cache TTL.
2. Resolve and validate the metadata's immutable FtM resource URL.
3. Stream the export to a temporary file.
4. Verify its published SHA-1 checksum for download integrity.
5. Transactionally replace the collection's local index, retaining metadata,
   import time, source URL, record count, and a local SHA-256.

SHA-1 is used to compare the publisher's resource checksum, not as a security
identity mechanism. Failed downloads, checksums, malformed records, empty
imports, and ambiguous referents do not replace the previous valid collection.
Deleted source records disappear when that collection is replaced.

The command refuses another sync less than six hours after that collection's
last import. `import-ftm` accepts a manually acquired snapshot; it records its
SHA-256 but does not have publisher metadata or a publisher checksum to verify.
Unsupported FtM schemas cause an explicit import failure rather than a partial
success that silently loses records.

## 11. Software licenses, CC0, and data licenses

The key distinction is **software licensing versus rights to the data**.

FtM is MIT-licensed. Commercial use, modification, and redistribution of FtM
do not require buying a commercial software license. Retain the required
copyright and license notices when distributing copies or substantial portions.

Wikidata structured data and GLEIF LEI reference data are offered under CC0.
CC0 is a public-domain dedication: rights holders waive copyright and related
rights as far as legally possible. It generally allows free commercial and
non-commercial use, copying, modification, combination, and redistribution.
Attribution is not a CC0 condition, but provenance and credit are good practice.

CC0 does not guarantee accuracy, waive other people's privacy or trademark
rights, or remove API access rules and rate limits. Combining CC0 data with
restricted data does not turn the whole result into CC0.

OpenSanctions bulk data is available for non-commercial use under its applicable
terms, commonly CC-BY-NC-4.0 with upstream terms to review. Preserve OpenSanctions
and underlying-source attribution. "Open" does not mean all of its data is CC0.
The current project's non-commercial data constraint comes from this usage,
not from FtM's MIT software license.

## 12. OpenSanctions commercial licensing research

These are descriptions of published offerings, not a quote or a negotiated
agreement for this project:

| Route | Typical use | Commercial arrangement |
|---|---|---|
| Hosted Screening API | Send queries and present results, including inside a product sold to customers under the standard API terms. | Prepaid query credits. The current FAQ says a bulk reseller license is not required for this API usage; passing on the collection or customers' own API access is restricted. |
| Internal screening license | Run bulk data locally for an organization's own supplier checks or internal knowledge graph. | Individually quoted monthly or annual flat rate; not a general right to serve customers or redistribute the collection. |
| Reseller / OEM license | Embed bulk data in a product or service serving customers. | Individually quoted agreement defining distribution and permitted use. |

The bulk agreement consists of published general terms plus an individual offer.
The offer defines the scope and price and takes precedence where it differs.
The licensing pages reviewed did not provide a fixed public bulk-license price.

Material provisions in the published data terms included:

- Internal rights are time-limited, non-transferable, and non-sublicensable.
  Disclosure restrictions can also affect affiliated companies.
- The standard terms restrict editing or supplementing the collection. Clarify
  permitted normalization, graph enrichment, report exports, and redistribution
  rather than assuming a commercial contract grants every conceivable use.
- Monthly billing has a three-month minimum term and one-month notice;
  annual billing has a one-year minimum term and three-month notice, subject
  to the individual offer and the rest of the agreement.
- Use rights end on termination; electronic copies generally must be deleted,
  with an exception stated for overall archival backups.
- Section 11 specifies a EUR 100,000-per-breach contractual penalty for certain
  unauthorized uses or false statements about usage, with further qualifications.
  This is a contract clause, not a subscription price or a determination of
  enforceability in any particular situation.

The free non-commercial bulk route, not a paid commercial agreement, is the
current project's intended route. A future commercial deployment, even for
internal business use rather than a sold product, requires a fresh review of
the relevant terms. There was no decision to purchase a license or commercialize
the prototype. Seek qualified advice before entering an agreement.

Primary references reviewed:

- [FtM MIT license](https://github.com/opensanctions/followthemoney/blob/main/LICENSE)
- [CC0 explanation and legal links](https://creativecommons.org/publicdomain/zero/1.0/)
- [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)
- [GLEIF data terms](https://www.gleif.org/en/meta/lei-data-terms-of-use)
- [OpenSanctions bulk documentation](https://www.opensanctions.org/docs/bulk/)
- [OpenSanctions update and download guidance](https://www.opensanctions.org/docs/bulk/updates/)
- [OpenSanctions topic definitions](https://www.opensanctions.org/docs/topics/)
- [OpenSanctions licensing overview](https://www.opensanctions.org/licensing/)
- [Commercial FAQ](https://www.opensanctions.org/docs/commercial/faq/)
- [Preparing for a licensing conversation](https://www.opensanctions.org/docs/commercial/questions/)
- [Published bulk-data terms, effective September 1, 2025](https://www.opensanctions.org/docs/terms/data/202509/)

## 13. Operating the CLI and understanding the output

Run the executable, subcommand, and company name on the same PowerShell line:

```powershell
.\.venv\Scripts\supplier-insights.exe lookup "Microsoft" > lookup.json
```

`lookup` alone is not a PowerShell command. Running the executable with `lookup`
but no company name produces a missing-argument error. Quotes are useful for
multi-word names.

The redirection above sends only stdout to the ignored `lookup.json` file.
The readable tree and warnings remain visible on stderr. An ambiguity candidate
list is a selection step, not a list of subsidiaries. `--qid` and `--lei` can
explicitly resolve source selection; `--no-tree` suppresses the readable output.

First runs of a large company can take several minutes. HTTP calls are serialized
within a client, with one-second spacing per host, a default 60-second HTTP
timeout, and up to three attempts for retryable JSON requests. Cached repeats
should be faster. The cache's default TTL is 24 hours; request provenance includes
the original fetch time and whether a cached response was used.

There is currently no progress indicator. The CLI assembles the report before
printing the tree, so a quiet terminal can mean that collection is still running.
It is not a guarantee of progress: source timeouts and warnings still need review.

## 14. Local files, privacy, and reproducibility

`SUPPLIER_INSIGHTS_DATA_DIR` defaults to `.supplier-insights` relative to the
working directory. Use a consistent directory when expecting cache reuse or
the same imported screening collections.

| File | Purpose |
|---|---|
| `cache.sqlite` | Successful JSON response bodies and fetch timestamps. |
| `graph.sqlite` | Last lookup's nodes, roots, labeled edges, provenance, and warnings; replaced transactionally. |
| `screening.sqlite` | Imported collections, entity bodies, matching terms, references, aliases, and snapshot metadata. |
| `lookup.json` | Optional retained report, created by shell redirection. |

These files are local data, not source code. `.gitignore` excludes the default
data directory, SQLite files, FtM exports, `lookup.json`, `.venv`, and real `.env`
files. Only `.env.example` is intended to be committed. Other filenames for
exports or custom data directories need equivalent care; they are not all
automatically ignored.

Do not commit keys, downloaded screening data, personal records, caches, or
terminal transcripts. Local screening data can contain personal information.
The public endpoints used here require no API keys.

CLI output and local snapshots are more useful evidence than an assumption that
the same live query will always return the same corporate structure. Keep fetch
times, snapshot metadata, original identifiers, and warnings when interpreting
a retained report.

## 15. What was demonstrated during the session

- The initial behavior tests failed before implementation. Subsequent
  regression tests reproduced exact-name selection, product/patent filtering,
  reverse-query partial failures, cross-source depth handling, and ambiguous
  referent handling before the corresponding fixes.
- The final implementation at knowledge-capture time had 46 passing tests,
  with mocked HTTP and synthetic screening records. Ruff checks, formatting
  checks, locked dependency setup, and wheel/source-distribution builds passed
  during the working session.
- Live Siemens lookup exercised Wikidata/GLEIF joins, bounded traversal,
  reporting exceptions, readable/JSON output, and persisted SQLite graph data.
- Live Microsoft lookup resolved Q2283, joined LEI INR2EJN1ERAN0W5ZP974, and
  included GitHub (Q364), LinkedIn Corporation (Q127278673), and ZeniMax Media
  (Q189557). The corrected bounded run contained 90 nodes and excluded the
  patent/software ownership targets observed in the earlier run.
- A World Bank debarment export was downloaded from its metadata's immutable
  resource, checksum-verified, and imported as 4,346 FtM entities. This was an
  integration exercise in a separate local test data directory, not evidence
  that every normal lookup directory has a snapshot installed.
- The user confirmed that the corrected Microsoft output looked substantially
  better and clarified that waiting several minutes on a first run was acceptable.

Live record counts and relationships are observations from that session, not
stable acceptance thresholds or a commitment that the same entities will always
be present. The GitHub Actions workflow is configured to run tests, lint,
format checks, and packaging on pushes and pull requests; local results alone
do not establish the outcome of a later hosted run.

## 16. Known gaps and candidate next work

These are not implemented or committed product promises:

- Progress reporting during slow collection; clearer phase/timing information
  would address the impression that the CLI has frozen.
- More complete corporate discovery beyond the raw reverse-query window,
  without reintroducing global SPARQL traversals that time out.
- Better handling of multilingual labels, historical ownership, conflicting
  sources, and organizations missing useful classifications.
- Measured performance and compatibility testing for large OpenSanctions
  collections, plus explicit freshness policy beyond the retained timestamps.
- Broader relationship traversal and improved identity matching with auditable
  confidence, without relaxing the shared-identifier merge invariant.
- Optional graph visualization or an OpenAleph evaluation
  ([`openaleph/openaleph`](https://github.com/openaleph/openaleph)).
  Do not substitute legacy `alephdata/aleph`; the project brief explicitly
  excluded that end-of-life platform.
- Potential additional sources: UK Companies House PSC filings under the Open
  Government Licence, SEC EDGAR Exhibit 21 subsidiary lists via edgartools, and
  Open Supply Hub factory locations. These were starting research directions,
  not integrations or independently verified implementation commitments.
- Choose an explicit software license for this repository if redistribution or
  open-source publication beyond source visibility is intended. Review data
  rights independently, especially before business use or redistribution.

For code navigation, setup, and repeatable commands, start with the
[`README`](../README.md). For licensing and attribution maintenance, use
[`DATA_SOURCES.md`](../DATA_SOURCES.md).
