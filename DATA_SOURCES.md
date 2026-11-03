# Data sources and attribution

This prototype is for **personal, non-commercial research only**. Software
dependencies and data have separate licenses. Do not treat the combined output
as CC0: OpenSanctions material retains its non-commercial and attribution terms.
No data snapshots are committed to this repository.

| Source | License / terms | Attribution and use |
|---|---|---|
| Wikidata | Structured data: [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing) | Wikidata contributors; preserve QIDs and entity URLs. [API](https://www.wikidata.org/w/api.php), [SPARQL](https://query.wikidata.org/). Use a descriptive User-Agent, serialized requests, caching, bounded one-hop queries, and backoff. Labels and claims are incomplete and may be outdated. |
| GLEIF | LEI reference data: [CC0](https://www.gleif.org/en/meta/lei-data-terms-of-use) | Global Legal Entity Identifier Foundation (GLEIF); preserve LEIs and record URLs. [API](https://api.gleif.org/api/v1/lei-records). Level 2 parentage means accounting consolidation, not ownership percentages. Reporting exceptions and missing LEIs limit coverage. |
| OpenSanctions | Bulk data free for non-commercial use; generally [CC-BY-NC-4.0](https://creativecommons.org/licenses/by-nc/4.0/); verify each upstream dataset's terms | Attribute OpenSanctions and the underlying datasets/records. [Bulk documentation](https://www.opensanctions.org/docs/bulk/), [licensing](https://www.opensanctions.org/licensing/), [dataset catalog](https://www.opensanctions.org/datasets/). JSON results preserve dataset IDs, record URLs, snapshot metadata, and source URLs. Do not redistribute without checking the applicable terms. Commercial use needs a separate license. |
| FollowTheMoney | [MIT software](https://github.com/opensanctions/followthemoney/blob/main/LICENSE) | FtM entity model and normalization library; this software license does not license imported data. Commercial use of the software does not require buying a commercial FtM license. |

CC0 is a public-domain dedication that generally permits commercial and
non-commercial reuse, modification, and redistribution without an attribution
condition. Preserve provenance as good practice. CC0 does not guarantee accuracy,
override privacy/trademark rights, or remove API access rules and rate limits.

Our OpenSanctions integration uses bulk files, not its paid hosted API. A future
commercial deployment must distinguish internal bulk-data use, reseller/OEM use,
and hosted API use under their respective terms. The
[project knowledge capture](docs/PROJECT_KNOWLEDGE.md#12-opensanctions-commercial-licensing-research)
records the November 2, 2026 licensing research and links to the primary terms;
it is not a legal opinion or a quote. A software license for this repository's own
code is a separate decision and was not selected during the kickoff.

## OpenSanctions collection selection

The explicit `sync` command defaults to the `default` collection, as recommended
by OpenSanctions for enrichment and connected entities. It can be large.
Alternative dataset/collection IDs can be supplied explicitly. Relevant catalog
entries include PEP collections, EITI SOEs, World Bank debarred firms, and GEM
energy ownership; check the current catalog for coverage and licensing rather
than assuming all are present in every export.

Downloads use `https://data.opensanctions.org/datasets/latest/<dataset>/`.
The resource URL, dataset metadata (including available license and attribution
fields), import time, and SHA-256 are retained locally and included in reports.
Re-sync at most every six hours; imports replace a collection transactionally
so deleted records do not remain as stale hits.
Sync resolves the immutable resource URL from fresh metadata and verifies the
published SHA-1 checksum before importing. A SHA-256 is additionally retained
for local provenance. A failed download, checksum, or import preserves the
previous collection.

## Interpretation and limitations

- Name matches are **unverified candidates**, never automatic identity merges.
  LEIs and Wikidata QIDs are the supported cross-source identifiers.
- Headquarters, registration/associated countries, and explicitly documented
  operating areas are different facts. A subsidiary's address does not establish
  its parent's operating countries. Missing operating-country data means unknown.
- Wikidata parent organizations, explicit ownership, and GLEIF consolidation
  relationships are labeled separately. No percentages are invented.
- A PEP connection, state ownership, sanction, or debarment is an attributed
  source assertion, not a judgment about wrongdoing. Historical statements can
  exist in source data. Review dates and original records.
- No local match does not establish absence of a connection or provide clearance.
  Coverage, snapshot age, matching limitations, traversal depth, and limits matter.
- OpenAleph (`openaleph/openaleph`, MIT) may be evaluated later. Legacy
  `alephdata/aleph` is out of scope. Companies House, EDGAR, and Open Supply Hub
  are not integrated in this MVP.
