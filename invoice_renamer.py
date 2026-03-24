#!/usr/bin/env python3
"""Rename invoice PDFs based on a regex extracted invoice number."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

try:
    import pymupdf as fitz  # Preferred import for modern PyMuPDF.
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import fitz  # type: ignore[no-redef]

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - runtime fallback
    import tomli as tomllib  # type: ignore[no-redef]


INVALID_FILENAME_CHARS = r'<>:"/\|?*'
INVALID_FILENAME_TABLE = str.maketrans({char: "_" for char in INVALID_FILENAME_CHARS})


@dataclass(frozen=True)
class AppConfig:
    invoice_number_regex: str
    filename_template: str
    recursive: bool = False
    preserve_pdf_extension: bool = True


@dataclass
class Counters:
    processed: int = 0
    renamed: int = 0
    skipped: int = 0
    errors: int = 0


def out(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        # Fallback for non-UTF-8 Windows terminals.
        encoded = message.encode(sys.stdout.encoding or "utf-8", errors="replace")
        print(encoded.decode(sys.stdout.encoding or "utf-8", errors="replace"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract invoice number from PDFs and rename files."
    )
    parser.add_argument("folder_path", help="Folder containing invoice PDF files.")
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Path to TOML config file. Default: config.toml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview renames without making changes.",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> AppConfig:
    if not config_path.is_file():
        raise ValueError(f"Config file not found: {config_path}")

    with config_path.open("rb") as file:
        raw = tomllib.load(file)

    extract = raw.get("extract", {})
    rename = raw.get("rename", {})
    scan = raw.get("scan", {})

    invoice_regex = extract.get("invoice_number_regex")
    filename_template = rename.get("filename_template")
    preserve_pdf_extension = rename.get("preserve_pdf_extension", True)
    recursive = scan.get("recursive", False)

    if not isinstance(invoice_regex, str) or not invoice_regex.strip():
        raise ValueError("Missing required config key: [extract].invoice_number_regex")
    if not isinstance(filename_template, str) or not filename_template.strip():
        raise ValueError("Missing required config key: [rename].filename_template")
    if "{{invoice_number}}" not in filename_template:
        raise ValueError(
            "Config [rename].filename_template must contain {{invoice_number}}"
        )

    try:
        re.compile(invoice_regex)
    except re.error as exc:
        raise ValueError(
            f"Invalid [extract].invoice_number_regex pattern: {exc}"
        ) from exc

    return AppConfig(
        invoice_number_regex=invoice_regex,
        filename_template=filename_template,
        recursive=bool(recursive),
        preserve_pdf_extension=bool(preserve_pdf_extension),
    )


def iter_pdf_files(folder_path: Path, recursive: bool) -> Iterable[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    for path in sorted(folder_path.glob(pattern)):
        if path.is_file() and path.suffix.lower() == ".pdf":
            yield path


def extract_text(pdf_path: Path) -> str:
    content: list[str] = []
    with fitz.open(pdf_path) as document:
        for page in document:
            content.append(page.get_text())
    return "\n".join(content)


def extract_invoice_number(text: str, regex_pattern: str) -> str | None:
    match = re.search(regex_pattern, text, flags=re.MULTILINE)
    if not match:
        return None

    if "invoice_number" in match.groupdict() and match.group("invoice_number"):
        return match.group("invoice_number").strip()
    if match.lastindex and match.lastindex >= 1:
        first_group = match.group(1)
        if first_group:
            return first_group.strip()
    return match.group(0).strip()


def sanitize_filename(filename_stem: str) -> str:
    sanitized = filename_stem.translate(INVALID_FILENAME_TABLE)
    sanitized = sanitized.rstrip(" .")
    if not sanitized:
        sanitized = "unnamed"
    return sanitized


def build_target_name(invoice_number: str, config: AppConfig) -> str:
    stem = config.filename_template.replace("{{invoice_number}}", invoice_number)
    stem = sanitize_filename(stem)
    if config.preserve_pdf_extension:
        return f"{stem}.pdf"
    return stem if stem.lower().endswith(".pdf") else f"{stem}.pdf"


def rename_pdfs(
    folder_path: Path,
    config: AppConfig,
    dry_run: bool,
    logger: Callable[[str], None] = out,
) -> Counters:
    counters = Counters()

    for pdf_path in iter_pdf_files(folder_path, recursive=config.recursive):
        counters.processed += 1
        source_name = pdf_path.name

        try:
            text = extract_text(pdf_path)
            invoice_number = extract_invoice_number(text, config.invoice_number_regex)
            if not invoice_number:
                counters.skipped += 1
                logger(f"{source_name} -> [SKIPPED: no regex match]")
                continue

            target_name = build_target_name(invoice_number, config)
            target_path = pdf_path.with_name(target_name)

            if target_path == pdf_path:
                counters.skipped += 1
                logger(f"{source_name} -> {target_name} [SKIPPED: unchanged]")
                continue

            if target_path.exists():
                counters.skipped += 1
                logger(f"{source_name} -> {target_name} [SKIPPED: target exists]")
                continue

            if dry_run:
                counters.skipped += 1
                logger(f"{source_name} -> {target_name} [DRY-RUN]")
                continue

            pdf_path.rename(target_path)
            counters.renamed += 1
            logger(f"{source_name} -> {target_name}")
        except Exception as exc:  # pragma: no cover - runtime safety
            counters.errors += 1
            logger(f"{source_name} -> [ERROR: {exc}]")

    return counters


def main() -> int:
    args = parse_args()
    folder_path = Path(args.folder_path).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve()

    if not folder_path.is_dir():
        out(f"Error: folder does not exist or is not a directory: {folder_path}")
        return 2

    try:
        config = load_config(config_path)
    except Exception as exc:
        out(f"Error loading config: {exc}")
        return 2

    counters = rename_pdfs(folder_path, config, dry_run=args.dry_run)

    out(
        "\nSummary: "
        f"processed={counters.processed}, "
        f"renamed={counters.renamed}, "
        f"skipped={counters.skipped}, "
        f"errors={counters.errors}"
    )
    return 0 if counters.errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
