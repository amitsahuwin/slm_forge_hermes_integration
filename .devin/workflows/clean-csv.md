---
description: Clean CSV files by removing special characters (newlines, tabs) from cells for proper JSONL conversion
---

# Clean CSV Workflow

This workflow cleans CSV files by removing special characters (`\n`, `\r`, `\t`) from cells, ensuring each row converts to a single JSON entry when creating JSONL files.

## Prerequisites

- Python 3.10+
- No additional dependencies required (uses standard library only)

## Usage Steps

### 1. Basic Usage - Clean and create new file

Run the script with your CSV file path. This creates a new file with `_cleaned` suffix:

```bash
python scripts/data_utils/clean_csv.py /path/to/your/data.csv
```

**Output:** Creates `/path/to/your/data_cleaned.csv`

### 2. Specify custom output path

```bash
python scripts/data_utils/clean_csv.py /path/to/your/data.csv --output /path/to/output/cleaned_data.csv
```

### 3. Modify file in place (overwrites original)

```bash
python scripts/data_utils/clean_csv.py /path/to/your/data.csv --inplace
```

### 4. Remove only newlines (keep tabs)

```bash
python scripts/data_utils/clean_csv.py /path/to/your/data.csv --chars "\n"
```

### 5. Remove characters completely (no replacement)

```bash
python scripts/data_utils/clean_csv.py /path/to/your/data.csv --replacement ""
```

## All Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output` | `-o` | `<input>_cleaned.csv` | Output file path |
| `--inplace` | `-i` | `false` | Overwrite original file |
| `--chars` | `-c` | `\n\r\t` | Characters to remove |
| `--replacement` | `-r` | ` ` (space) | Replacement string |
| `--encoding` | `-e` | `utf-8` | File encoding |
| `--quiet` | `-q` | `false` | Suppress output |

## Example Output

```
✓ CSV cleaned successfully!
  Input:  /path/to/data.csv
  Output: /path/to/data_cleaned.csv
  Rows processed: 1500
  Cells processed: 7500
  Cells modified: 234
```

## Programmatic Usage

You can also import and use the function directly in Python:

```python
from scripts.data_utils.clean_csv import clean_csv_file

stats = clean_csv_file(
    input_path="/path/to/data.csv",
    output_path="/path/to/cleaned.csv",
    chars_to_remove="\n\r\t",
    replacement=" "
)
print(f"Modified {stats['cells_modified']} cells")
```
