# CSV Import Review

[![Checks](https://github.com/PatrickSchaller/csv-import-review/actions/workflows/checks.yml/badge.svg)](https://github.com/PatrickSchaller/csv-import-review/actions/workflows/checks.yml)

Review an inventory catalog before importing it. Keep valid rows, explain rejected rows, and reject every occurrence of a duplicated item code.

One Python file, the standard library, and an explicit input contract. No package installation, database, or web service is needed.

This is an independent portfolio example with invented data. It does not import into a live system or represent a client deployment.

## Try it

Requires Python 3.11 or newer. From the repository directory:

```sh
python csv_import_review.py examples/catalog.csv --output reviewed
```

The sample contains deliberate errors, so the command completes with **exit code 1**:

```text
Reviewed 12 rows: 5 accepted, 7 rejected.
Reports: reviewed
```

The new directory contains:

| File | Contents |
| --- | --- |
| `accepted.csv` | Valid, normalized rows, in input order |
| `rejected.csv` | Record numbers, physical line ranges, rejection reasons, and original cell values as JSON text |
| `summary.json` | Input filename and counts that reconcile |

See [the expected sample reports](examples/expected/summary.json) and [accepted rows](examples/expected/accepted.csv). Use a new output directory for each run; an existing path is refused.

## Input contract

UTF-8 CSV, optionally with a UTF-8 byte-order mark. The header contains exactly `sku`, `name`, and `quantity`, once each; header order may vary and names are case-sensitive. The file size limit is 10 MiB.

| Field | Rule |
| --- | --- |
| `sku` | 1–64 ASCII letters, digits, dots, underscores, or hyphens; first character must be a letter or digit |
| `name` | 1–200 characters after trimming, with no control characters or initial spreadsheet formula character (`=`, `+`, `-`, `@`) |
| `quantity` | ASCII digits representing a whole number from 0 to 1,000,000 |

Outer whitespace is trimmed. SKU case and leading zeroes are preserved; `HW-104` and `hw-104` are distinct codes. Quantities are written in canonical form: `0005` becomes `5`. Internal spaces in names remain unchanged. Other repairs are not inferred.

Names may contain Unicode and quoted commas. A multiline name is rejected under the control-character rule, while its physical source-line range remains visible in the report.

Duplicate detection uses the trimmed, case-sensitive SKU. Every occurrence is rejected, including an otherwise valid row whose duplicate has another error. Blank data records and wrong-length records are counted and rejected rather than skipped.

A structurally malformed CSV, invalid encoding, oversized input, or invalid header fails the file. Earlier valid-looking rows are not published as a partial success.

## Safe output

The source is opened for reading only. Reports are written to a temporary sibling directory and published together with a single rename after all writes succeed. The parent of the requested output directory must already exist.

Existing report directories are never merged or overwritten. Input/output collisions, an existing output file, and output symlinks are refused. A competing nonempty report that appears during publication is preserved. On POSIX systems, the operating system can replace a concurrently created **empty** directory at the final rename; no files can be replaced in that case.

Publication is atomic at the directory-name level on the local filesystem. It is not a promise of power-loss durability or identical behavior on every network filesystem.

Rejected values are encoded as a JSON array inside one CSV field. This preserves extra or missing cells and keeps formula-like source values away from the start of a spreadsheet cell. Accepted text follows the schema's formula-prefix restrictions. When opening accepted CSV in a spreadsheet, import the SKU column as text to preserve leading zeroes.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Review completed; all rows accepted, including a valid header-only file |
| `1` | Review completed; rejected rows need attention |
| `2` | Usage, input-file, or report-publication failure; no completed report was published by this run |

An exit code of 1 is a completed review, not an interrupted write. Automated imports should require code 0 or an explicit review of the rejected rows.

## Verify

```sh
python -m unittest discover -s tests -v
```

Checks cover duplicate handling, leading-zero identifiers, quoted commas, column order, UTF-8 BOMs, invalid quantities, source-line ranges, malformed input, deterministic results, write failures, publication races, and actual subprocess exit codes.

GitHub Actions runs the same suite on Windows and Linux with Python 3.11 and 3.13. Test count and hosted status are reported by the workflow rather than presented as a business outcome.

## Decisions and limits

- The schema is deliberately specific. This is not a general-purpose CSV cleaner or configurable mapping framework.
- The bounded catalog is held in memory so an early row can be rejected when a later duplicate is found. Larger datasets would need disk-backed indexing or another bounded-memory design.
- Rejection reasons do not echo entire source rows into the console. The detailed local report retains the original parsed cell values; the original file remains the byte-for-byte source of truth.
- Reports are deterministic for the same filename and content. There is no timestamp or absolute local path in the summary.

The initial implementation was prepared with Codex assistance. The source, assumptions, examples, and tests are included for review and modification.

## License

MIT. See [LICENSE](LICENSE).
