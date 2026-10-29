"""Review a small inventory CSV without changing its source or importing data."""

import argparse
import csv
import io
import json
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("sku", "name", "quantity")
MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_QUANTITY = 1_000_000
SKU = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
WHOLE_NUMBER = re.compile(r"[0-9]+")


@dataclass
class ReviewedRow:
    number: int
    line_start: int
    line_end: int
    original_values: list[str]
    sku: str
    name: str
    quantity: str
    reasons: list[str]


def review(source: Path) -> list[ReviewedRow]:
    if not source.is_file():
        raise ValueError("Input must be an existing regular file.")
    with source.open("rb") as stream:
        content = stream.read(MAX_INPUT_BYTES + 1)
    if len(content) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 10 MiB catalog limit.")
    reader = csv.reader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None or len(header) != 3 or set(header) != set(FIELDS):
            raise ValueError("Header must contain sku, name, and quantity exactly once; order may vary.")
        positions = [header.index(field) for field in FIELDS]
        rows = []
        previous_line = reader.line_num
        # shortcut: keep this bounded catalog in memory to reject every duplicate; use disk-backed indexing for larger inputs.
        for number, values in enumerate(reader, 1):
            start, end = previous_line + 1, reader.line_num
            previous_line = end
            sku, name, quantity = [values[i].strip() if i < len(values) else "" for i in positions]
            reasons = []
            if len(values) != len(FIELDS):
                reasons.append(f"Expected 3 fields; found {len(values)}.")
            else:
                if not SKU.fullmatch(sku):
                    reasons.append("SKU must start with an ASCII letter or digit and use at most 64 letters, digits, dots, underscores, or hyphens.")
                if not name or len(name) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in name):
                    reasons.append("Name must contain 1 to 200 characters without control characters.")
                elif name[0] in "=+-@":
                    reasons.append("Name cannot begin with a spreadsheet formula character (=, +, -, or @).")
                digits = quantity.lstrip("0") or "0"
                if not WHOLE_NUMBER.fullmatch(quantity) or len(digits) > 7 or int(digits) > MAX_QUANTITY:
                    reasons.append("Quantity must be a whole number from 0 to 1000000, using ASCII digits only.")
                else:
                    quantity = str(int(digits))
            rows.append(ReviewedRow(number, start, end, values, sku, name, quantity, reasons))
    except csv.Error as error:
        raise ValueError(f"Malformed CSV near physical line {reader.line_num}: {error}") from error

    counts = Counter(row.sku for row in rows if row.sku)
    for row in rows:
        if row.sku and counts[row.sku] > 1:
            row.reasons.append("SKU occurs more than once; all occurrences are rejected.")
    return rows


def write_reports(source: Path, destination: Path, rows: list[ReviewedRow]) -> dict:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Output already exists. Choose a new output directory.")
    destination = destination.resolve()
    if not destination.parent.is_dir():
        raise ValueError("Output parent directory must already exist.")
    accepted = [row for row in rows if not row.reasons]
    rejected = [row for row in rows if row.reasons]
    summary = {
        "input_file": source.name,
        "input_rows": len(rows),
        "accepted_rows": len(accepted),
        "rejected_rows": len(rejected),
    }
    with tempfile.TemporaryDirectory(prefix=".csv-review-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        with (staging / "accepted.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(FIELDS)
            writer.writerows((row.sku, row.name, row.quantity) for row in accepted)
        with (staging / "rejected.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("record_number", "line_start", "line_end", "reasons", "original_values_json"))
            writer.writerows((row.number, row.line_start, row.line_end, " ".join(row.reasons),
                              json.dumps(row.original_values, ensure_ascii=False)) for row in rejected)
        with (staging / "summary.json").open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Output appeared during review. Existing results were left unchanged.")
        # shortcut: POSIX can replace a racing empty directory; use no-replace rename primitives if empty-directory reservation matters.
        staging.rename(destination)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="UTF-8 inventory CSV")
    parser.add_argument("--output", required=True, type=Path, help="new report directory; its parent must exist")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("Output already exists. Choose a new output directory.")
        rows = review(args.input)
        summary = write_reports(args.input, args.output, rows)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Review failed: {error}", file=sys.stderr)
        return 2
    print(f"Reviewed {summary['input_rows']} rows: {summary['accepted_rows']} accepted, {summary['rejected_rows']} rejected.")
    print(f"Reports: {args.output}")
    return 1 if summary["rejected_rows"] else 0


if __name__ == "__main__":
    sys.exit(main())
