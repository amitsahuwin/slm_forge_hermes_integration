#!/usr/bin/env python3
"""
CSV Cleaner Script

Removes special characters (newlines, carriage returns, tabs) from CSV cells
to ensure proper JSONL conversion where each row becomes a single JSON entry.

Usage:
    python clean_csv.py <input_csv_path> [--output <output_path>] [--inplace]

Examples:
    # Clean and save to new file (default: adds _cleaned suffix)
    python clean_csv.py /path/to/data.csv

    # Clean and save to specific output file
    python clean_csv.py /path/to/data.csv --output /path/to/cleaned_data.csv

    # Clean in place (overwrites original)
    python clean_csv.py /path/to/data.csv --inplace

    # Remove only newlines (default removes \\n, \\r, \\t)
    python clean_csv.py /path/to/data.csv --chars "\\n"

    # Custom replacement (replace with space instead of removing)
    python clean_csv.py /path/to/data.csv --replacement " "
"""

import argparse
import csv
import re
import sys
from pathlib import Path


def clean_cell(value: str, chars_to_remove: str, replacement: str) -> str:
    """
    Clean a single cell value by removing/replacing special characters.

    Args:
        value: The cell value to clean
        chars_to_remove: String of characters to remove (e.g., "\\n\\r\\t")
        replacement: String to replace removed characters with

    Returns:
        Cleaned cell value
    """
    if not isinstance(value, str):
        return value

    # Build regex pattern from characters to remove
    # Escape special regex characters
    pattern = "[" + re.escape(chars_to_remove) + "]"
    cleaned = re.sub(pattern, replacement, value)

    # Optionally collapse multiple spaces into one
    if replacement == " ":
        cleaned = re.sub(r" +", " ", cleaned)

    return cleaned.strip()


def clean_csv_file(
    input_path: str,
    output_path: str | None = None,
    inplace: bool = False,
    chars_to_remove: str = "\n\r\t",
    replacement: str = " ",
    encoding: str = "utf-8",
) -> dict:
    """
    Clean a CSV file by removing special characters from all cells.

    Args:
        input_path: Path to the input CSV file
        output_path: Path for the output file (optional)
        inplace: If True, overwrite the original file
        chars_to_remove: Characters to remove from cells
        replacement: String to replace removed characters with
        encoding: File encoding

    Returns:
        Dictionary with statistics about the cleaning operation
    """
    input_file = Path(input_path)

    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if input_file.suffix.lower() != ".csv":
        print(f"Warning: File does not have .csv extension: {input_path}")

    # Determine output path
    if inplace:
        final_output = input_file
        temp_output = input_file.with_suffix(".csv.tmp")
    elif output_path:
        final_output = Path(output_path)
        temp_output = final_output
    else:
        # Default: add _cleaned suffix
        final_output = input_file.with_stem(input_file.stem + "_cleaned")
        temp_output = final_output

    # Statistics
    stats = {
        "total_rows": 0,
        "total_cells": 0,
        "cells_modified": 0,
        "input_file": str(input_file),
        "output_file": str(final_output),
    }

    # Read and clean
    rows = []
    with open(input_file, encoding=encoding, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            stats["total_rows"] += 1
            cleaned_row = []
            for cell in row:
                stats["total_cells"] += 1
                cleaned_cell = clean_cell(cell, chars_to_remove, replacement)
                if cleaned_cell != cell:
                    stats["cells_modified"] += 1
                cleaned_row.append(cleaned_cell)
            rows.append(cleaned_row)

    # Write cleaned data
    with open(temp_output, "w", encoding=encoding, newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    # Handle inplace replacement
    if inplace:
        temp_output.replace(final_output)

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Clean CSV files by removing special characters from cells.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("input_csv", help="Path to the input CSV file")

    parser.add_argument(
        "--output",
        "-o",
        help="Path for the output file (default: adds _cleaned suffix)",
    )

    parser.add_argument(
        "--inplace",
        "-i",
        action="store_true",
        help="Modify the file in place (overwrites original)",
    )

    parser.add_argument(
        "--chars",
        "-c",
        default="\n\r\t",
        help="Characters to remove (default: newline, carriage return, tab)",
    )

    parser.add_argument(
        "--replacement",
        "-r",
        default=" ",
        help="Replacement string (default: single space)",
    )

    parser.add_argument(
        "--encoding", "-e", default="utf-8", help="File encoding (default: utf-8)"
    )

    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress output messages"
    )

    args = parser.parse_args()

    # Parse escape sequences in chars argument
    chars = args.chars.encode().decode("unicode_escape")

    try:
        stats = clean_csv_file(
            input_path=args.input_csv,
            output_path=args.output,
            inplace=args.inplace,
            chars_to_remove=chars,
            replacement=args.replacement,
            encoding=args.encoding,
        )

        if not args.quiet:
            print("✓ CSV cleaned successfully!")
            print(f"  Input:  {stats['input_file']}")
            print(f"  Output: {stats['output_file']}")
            print(f"  Rows processed: {stats['total_rows']}")
            print(f"  Cells processed: {stats['total_cells']}")
            print(f"  Cells modified: {stats['cells_modified']}")

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error processing CSV: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
