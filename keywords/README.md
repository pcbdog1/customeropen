# PCBDOG Global Hardware Keyword Library

## Files

- `hardware_keyword_library.json`: maintained industry, product, job-title, procurement, query-template, and growth rules.
- `learned_keywords.json`: append-only location for new attributable hardware terms discovered later.
- `keyword_growth_state.json`: state schema for tracking three consecutive zero-result runs and used combinations.

## Intended Query Shape

Queries are assembled from:

```text
country + industry + product + job title + procurement term
```

Do not create the full Cartesian product. Select a bounded batch using no more than four products, three job titles, and three procurement terms for an industry. Rotate unused terms after a zero-result run.

## Growth Rules

After three consecutive zero-result runs:

1. Rotate to unused products in the current industry.
2. Rotate job-title and procurement terms.
3. Move to an adjacent hardware industry if the current category remains empty.
4. Add a newly discovered product phrase only when a public company source URL supports it.
5. Normalize and deduplicate terms case-insensitively.

Never add email addresses, personal names, leaked data, login-only data, pure-software terms, or PCB/PCBA manufacturing competitors to the keyword library.

## Integration Status

The production search-query boundary loads this library automatically. Existing industries remain first in the loop, and the library categories are appended after them.

Each bounded keyword-library query combines country, industry, product, job title, and procurement term. Three consecutive zero-result searches advance `expansion_level`, rotating the next product/job/procurement combination instead of stopping discovery.

New product, job-title, and procurement phrases are added to `learned_keywords.json` only from accepted public company results with an attributable source URL. Terms are normalized and deduplicated before writing.

The integration does not change workbook structure, lead deduplication, scoring thresholds, SMTP behavior, the ten-email cycle cap, or launchd scheduling.
