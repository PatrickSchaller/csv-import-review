import contextlib
import csv
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from csv_import_review import FIELDS, MAX_INPUT_BYTES, main, review

ROOT = Path(__file__).resolve().parents[1]


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "catalog.csv"
        self.output = self.root / "reviewed"

    def source_rows(self, rows, header=FIELDS):
        with self.source.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)
        return self.source

    def run_main(self, source=None, output=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main([str(source or self.source), "--output", str(output or self.output)])

    def test_preserves_leading_zero_sku_and_quoted_comma(self):
        self.source_rows([["00017", "Birch panel, 18 mm", "0"]])
        row, = review(self.source)
        self.assertEqual((row.sku, row.name, row.quantity), ("00017", "Birch panel, 18 mm", "0"))
        self.assertEqual(row.reasons, [])

    def test_accepts_bom_and_reordered_columns(self):
        self.source.write_bytes(b'\xef\xbb\xbfquantity,sku,name\r\n0005,00017,Handle\r\n')
        row, = review(self.source)
        self.assertEqual((row.sku, row.name, row.quantity), ("00017", "Handle", "5"))
        self.assertEqual(row.reasons, [])

    def test_trims_outer_whitespace_without_changing_sku_case(self):
        self.source_rows([[" abc-1 ", " Brass  handle ", " 0005 "]])
        row, = review(self.source)
        self.assertEqual((row.sku, row.name, row.quantity), ("abc-1", "Brass  handle", "5"))

    def test_all_duplicate_occurrences_are_rejected_even_if_one_is_invalid(self):
        self.source_rows([["A-1", "First", "1"], [" A-1 ", "Second", "bad"]])
        rows = review(self.source)
        self.assertTrue(all(any("all occurrences" in reason for reason in row.reasons) for row in rows))
        self.assertEqual(len(rows[1].reasons), 2)

    def test_sku_matching_is_case_sensitive(self):
        self.source_rows([["A-1", "First", "1"], ["a-1", "Second", "2"]])
        self.assertTrue(all(not row.reasons for row in review(self.source)))

    def test_missing_repeated_and_unknown_headers_fail_the_file(self):
        for header in (["sku", "name"], ["sku", "sku", "quantity"],
                       ["sku", "name", "quantity", "extra"], ["SKU", "name", "quantity"]):
            with self.subTest(header=header):
                self.source_rows([], header)
                self.assertEqual(self.run_main(), 2)
                self.assertFalse(self.output.exists())

    def test_blank_and_wrong_length_records_are_accounted_for(self):
        self.source.write_text("sku,name,quantity\n\nA-1,Missing quantity\nA-2,Extra,1,field\n", encoding="utf-8")
        rows = review(self.source)
        self.assertEqual([row.number for row in rows], [1, 2, 3])
        self.assertTrue(all(row.reasons for row in rows))

    def test_invalid_quantities_are_row_errors(self):
        for quantity in ("", "-1", "+1", "1.0", "1e3", "one", "１００", "1000001", "9" * 5000):
            with self.subTest(quantity=quantity[:20]):
                self.source_rows([["A-1", "Handle", quantity]])
                row, = review(self.source)
                self.assertTrue(any("Quantity" in reason for reason in row.reasons))

    def test_large_zero_padding_does_not_hit_python_integer_conversion_limit(self):
        self.source_rows([["A-1", "Handle", "0" * 5000 + "1"]])
        row, = review(self.source)
        self.assertEqual(row.quantity, "1")
        self.assertEqual(row.reasons, [])

    def test_invalid_names_and_formula_prefixes_are_rejected(self):
        for name in (" ", "x" * 201, "A\x00B", "A\nB", "=1+1", "+123", "-123", "@SUM(A1)"):
            with self.subTest(name=name[:20]):
                self.source_rows([["A-1", name, "1"]])
                row, = review(self.source)
                self.assertTrue(any("Name" in reason for reason in row.reasons))

    def test_invalid_skus_are_rejected(self):
        for sku in ("", "=1+1", "A B", "ä-1", "A" * 65):
            with self.subTest(sku=sku):
                self.source_rows([[sku, "Handle", "1"]])
                row, = review(self.source)
                self.assertTrue(any("SKU" in reason for reason in row.reasons))

    def test_multiline_record_keeps_record_number_and_physical_line_range(self):
        self.source.write_text('sku,name,quantity\nA-1,"Two\nlines",1\nA-2,Handle,2\n', encoding="utf-8")
        rows = review(self.source)
        self.assertEqual((rows[0].number, rows[0].line_start, rows[0].line_end), (1, 2, 3))
        self.assertEqual((rows[1].number, rows[1].line_start, rows[1].line_end), (2, 4, 4))
        self.assertTrue(rows[0].reasons)

    def test_structurally_malformed_csv_publishes_no_partial_report(self):
        self.source.write_text('sku,name,quantity\nA-1,Valid,1\nA-2,"Unclosed,2', encoding="utf-8")
        before = self.source.read_bytes()
        self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_invalid_encoding_and_empty_input_publish_nothing(self):
        for content in (b"", b"sku,name,quantity\nA-1,\xff,1"):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                self.assertEqual(self.run_main(), 2)
                self.assertFalse(self.output.exists())

    def test_input_size_limit_is_a_file_error(self):
        self.source.write_bytes(b"x" * (MAX_INPUT_BYTES + 1))
        self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.output.exists())

    def test_header_only_file_produces_a_zero_row_report(self):
        self.source_rows([])
        self.assertEqual(self.run_main(), 0)
        self.assertEqual(json.loads((self.output / "summary.json").read_text())["input_rows"], 0)

    def test_sample_counts_reconcile_and_accepted_values_are_explicit(self):
        source = ROOT / "examples" / "catalog.csv"
        before = source.read_bytes()
        self.assertEqual(self.run_main(source), 1)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary, {"input_file": "catalog.csv", "input_rows": 12, "accepted_rows": 5, "rejected_rows": 7})
        with (self.output / "accepted.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual(list(csv.reader(stream)), [list(FIELDS), ["00017", "Brass cabinet handle", "12"],
                ["WD-310", "Birch panel, 18 mm", "8"], ["HW-208", "Steel corner bracket", "24"],
                ["hw-104", "Brass handle", "5"], ["ZERO", "No stock yet", "0"]])
        with (self.output / "rejected.csv").open(encoding="utf-8", newline="") as stream:
            rejected = list(csv.DictReader(stream))
        self.assertEqual([int(row["record_number"]) for row in rejected], [4, 5, 6, 7, 8, 10, 11])
        self.assertEqual(source.read_bytes(), before)

    def test_rejected_values_are_json_text_and_preserve_the_original_cells(self):
        self.source_rows([["A-1", '=HYPERLINK("test","label")', "1"]])
        self.assertEqual(self.run_main(), 1)
        with (self.output / "rejected.csv").open(encoding="utf-8", newline="") as stream:
            row, = list(csv.DictReader(stream))
        self.assertTrue(row["original_values_json"].startswith("["))
        self.assertEqual(json.loads(row["original_values_json"]), ["A-1", '=HYPERLINK("test","label")', "1"])

    def test_repeat_runs_are_byte_deterministic(self):
        self.source_rows([["00017", "Handle", "5"], ["BAD", "", "2"]])
        second = self.root / "second"
        self.assertEqual(self.run_main(), 1)
        self.assertEqual(self.run_main(output=second), 1)
        for filename in ("accepted.csv", "rejected.csv", "summary.json"):
            self.assertEqual((self.output / filename).read_bytes(), (second / filename).read_bytes())

    def test_existing_empty_output_is_refused(self):
        self.source_rows([["A-1", "Handle", "1"]])
        self.output.mkdir()
        self.assertEqual(self.run_main(), 2)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_results_and_input_cannot_be_overwritten(self):
        self.source_rows([["A-1", "Handle", "1"]])
        self.assertEqual(self.run_main(), 0)
        original = {p.name: p.read_bytes() for p in self.output.iterdir()}
        self.assertEqual(self.run_main(), 2)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, original)
        before = self.source.read_bytes()
        self.assertEqual(self.run_main(output=self.source), 2)
        self.assertEqual(self.source.read_bytes(), before)

    def test_missing_parent_or_missing_input_creates_no_reports(self):
        self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.output.exists())
        self.source_rows([["A-1", "Handle", "1"]])
        missing = self.root / "not-created" / "report"
        self.assertEqual(self.run_main(output=missing), 2)
        self.assertFalse(missing.parent.exists())

    def test_write_failure_removes_staging_and_publishes_nothing(self):
        self.source_rows([["A-1", "Handle", "1"]])
        with patch("csv_import_review.json.dump", side_effect=OSError("Simulated full disk")):
            self.assertEqual(self.run_main(), 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".csv-review-*")), [])

    def test_competing_report_created_at_publication_is_preserved(self):
        self.source_rows([["A-1", "Handle", "1"]])
        original_rename = Path.rename

        def concurrent_report(staging, destination):
            destination.mkdir()
            (destination / "owner.txt").write_text("Existing results", encoding="utf-8")
            return original_rename(staging, destination)

        with patch("csv_import_review.Path.rename", new=concurrent_report):
            self.assertEqual(self.run_main(), 2)
        self.assertEqual((self.output / "owner.txt").read_text(), "Existing results")
        self.assertEqual(len(list(self.output.iterdir())), 1)
        self.assertEqual(list(self.root.glob(".csv-review-*")), [])

    def test_command_line_exit_codes_work_from_another_directory(self):
        self.source_rows([["A-1", "Handle", "1"]])
        command = [sys.executable, str(ROOT / "csv_import_review.py"), str(self.source), "--output", str(self.output)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 accepted, 0 rejected", result.stdout)
        self.assertEqual(subprocess.run(command, cwd=self.root, capture_output=True).returncode, 2)
        command[2] = str(ROOT / "examples" / "catalog.csv")
        command[-1] = str(self.root / "rejected-review")
        self.assertEqual(subprocess.run(command, cwd=self.root, capture_output=True).returncode, 1)


if __name__ == "__main__":
    unittest.main()
