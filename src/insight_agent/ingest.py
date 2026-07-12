"""Deterministic ingestion: decide which tables a data directory contains.

Scanning a directory of arbitrary files and turning them into DuckDB-loadable
CSV tables is a pure data operation with no LLM involvement, exactly like the
tools in insight_agent.tools. CSV files are used directly. Other supported
formats are converted once into a cached CSV under a `.converted` directory
next to the source files, and the cache is invalidated by comparing
modification times, so editing a source file takes effect on the next scan.
Unsupported extensions are skipped, never raised as an error.
"""

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_CONVERTED_DIR_NAME = ".converted"

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_NON_IDENTIFIER_RE = re.compile(r"[^a-z0-9_]+")


@dataclass(frozen=True)
class TableSource:
    """One table to load into DuckDB: its name and the CSV file backing it."""

    table_name: str
    csv_path: Path


@dataclass(frozen=True)
class SkippedFile:
    """One file a scan could not turn into a table: its name and the reason."""

    file: str
    reason: str


@dataclass(frozen=True)
class Discovery:
    """The outcome of scanning a data directory.

    tables lists every table the scan can load; skipped lists every file it
    could not use, either because its extension is unsupported or because its
    conversion failed.
    """

    tables: list[TableSource]
    skipped: list[SkippedFile]


def _sanitize_identifier(label: str) -> str:
    """Turn an arbitrary label into a lowercase, SQL-safe identifier.

    Non-identifier characters become underscores, and a label that would
    start with a digit (or reduce to nothing) is prefixed so the result
    always matches ^[a-z_][a-z0-9_]*$.
    """
    slug = _NON_IDENTIFIER_RE.sub("_", label.strip().lower()).strip("_")
    if not slug:
        slug = "t"
    if not _IDENTIFIER_RE.match(slug):
        slug = f"t_{slug}"
    return slug


def _deduplicate(labels: list[str]) -> list[str]:
    """Resolve name collisions with deterministic numeric suffixes.

    Labels are processed in the order given; the first occurrence of a name
    keeps it, later occurrences get _2, _3, and so on.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for label in labels:
        count = seen.get(label, 0) + 1
        seen[label] = count
        result.append(label if count == 1 else f"{label}_{count}")
    return result


def _needs_conversion(source: Path, cached: Path) -> bool:
    """Report whether a cached CSV is missing or older than its source file."""
    return not cached.exists() or source.stat().st_mtime > cached.stat().st_mtime


def _sniff_delimiter(path: Path) -> str:
    """Guess the field delimiter of a plain-text table file.

    Falls back to tab for .tsv files and comma for anything else when the
    sample is too short or too irregular for csv.Sniffer to decide.
    """
    sample = path.read_text(encoding="utf-8", errors="replace")[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return "\t" if path.suffix.lower() == ".tsv" else ","


def _convert_excel(path: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """Convert every sheet of a workbook into its own cached CSV.

    A single-sheet workbook is labeled with the file stem alone; a multi-sheet
    workbook labels each sheet as "<stem>_<sheet>".
    """
    workbook = pd.ExcelFile(path)
    sheet_names = workbook.sheet_names
    single_sheet = len(sheet_names) == 1
    outputs: list[tuple[str, Path]] = []
    for sheet_name in sheet_names:
        label = path.stem if single_sheet else f"{path.stem}_{sheet_name}"
        cache_path = cache_dir / f"{_sanitize_identifier(label)}.csv"
        if _needs_conversion(path, cache_path):
            frame = workbook.parse(sheet_name)
            cache_dir.mkdir(parents=True, exist_ok=True)
            frame.to_csv(cache_path, index=False)
        outputs.append((label, cache_path))
    return outputs


def _convert_delimited_text(path: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """Convert a delimiter-sniffed plain-text table into a cached CSV."""
    label = path.stem
    cache_path = cache_dir / f"{_sanitize_identifier(label)}.csv"
    if _needs_conversion(path, cache_path):
        delimiter = _sniff_delimiter(path)
        frame = pd.read_csv(path, sep=delimiter, engine="python")
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_path, index=False)
    return [(label, cache_path)]


def _convert_json(path: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """Convert a JSON records file into a cached CSV."""
    label = path.stem
    cache_path = cache_dir / f"{_sanitize_identifier(label)}.csv"
    if _needs_conversion(path, cache_path):
        frame = pd.read_json(path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_path, index=False)
    return [(label, cache_path)]


def _convert_parquet(path: Path, cache_dir: Path) -> list[tuple[str, Path]]:
    """Convert a Parquet file into a cached CSV."""
    label = path.stem
    cache_path = cache_dir / f"{_sanitize_identifier(label)}.csv"
    if _needs_conversion(path, cache_path):
        frame = pd.read_parquet(path)
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_csv(cache_path, index=False)
    return [(label, cache_path)]


_CONVERTERS = {
    ".xlsx": _convert_excel,
    ".xls": _convert_excel,
    ".txt": _convert_delimited_text,
    ".tsv": _convert_delimited_text,
    ".json": _convert_json,
    ".parquet": _convert_parquet,
}


def scan_tables(data_dir: Path) -> Discovery:
    """Scan data_dir and return the tables to load plus the files skipped.

    Direct children of data_dir only are scanned (the .converted cache
    directory is a subdirectory and is therefore never scanned as a source).
    CSV files are used as-is. Files with a supported non-CSV extension
    (.xlsx, .xls, .txt, .tsv, .json, .parquet) are converted to a cached CSV
    under <data_dir>/.converted/, reconverted whenever the source file is
    newer than its cached copy. Files with any other extension, and files
    whose conversion fails, are reported in the skipped list with their
    reason and logged at INFO. Table names are sanitized SQL identifiers
    derived from the file stem (or "<stem>_<sheet>" for a multi-sheet
    workbook); collisions are resolved with deterministic numeric suffixes,
    assigned in sorted file order.
    """
    if not data_dir.exists():
        return Discovery(tables=[], skipped=[])
    cache_dir = data_dir / _CONVERTED_DIR_NAME
    candidates: list[tuple[str, Path]] = []
    skipped: list[SkippedFile] = []
    for path in sorted(p for p in data_dir.iterdir() if p.is_file()):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            candidates.append((path.stem, path))
        elif suffix in _CONVERTERS:
            try:
                candidates.extend(_CONVERTERS[suffix](path, cache_dir))
            except Exception as exc:
                logger.info("conversion failed for %s: %s", path, exc)
                skipped.append(SkippedFile(file=path.name, reason=f"conversion failed: {exc}"))
        else:
            logger.info("skipping unsupported file: %s", path)
            skipped.append(
                SkippedFile(file=path.name, reason=f"unsupported file type: {suffix or 'none'}")
            )
    names = _deduplicate([_sanitize_identifier(label) for label, _ in candidates])
    tables = [
        TableSource(table_name=name, csv_path=csv_path)
        for name, (_, csv_path) in zip(names, candidates, strict=True)
    ]
    return Discovery(tables=tables, skipped=skipped)


def discover_tables(data_dir: Path) -> list[TableSource]:
    """Scan data_dir and return the tables to load, each as a table name and CSV path.

    The tables half of scan_tables, for callers that do not need the skipped
    files.
    """
    return scan_tables(data_dir).tables
