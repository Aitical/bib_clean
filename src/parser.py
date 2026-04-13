import re
import logging
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# BibTeX mandatory fields by type (or mandatory field groups).
# Each inner list means "at least one of these fields must exist".
ENTRY_REQUIRED_FIELD_GROUPS: Dict[str, List[List[str]]] = {
    "article": [["author"], ["title"], ["journal"], ["year"]],
    "book": [["title"], ["publisher"], ["year"], ["author", "editor"]],
    "inproceedings": [["author"], ["title"], ["booktitle"], ["year"]],
    "conference": [["author"], ["title"], ["booktitle"], ["year"]],
    "incollection": [["author"], ["title"], ["booktitle"], ["publisher"], ["year"]],
    "inbook": [["title"], ["publisher"], ["year"], ["author", "editor"], ["chapter", "pages"]],
    "proceedings": [["title"], ["year"]],
    "phdthesis": [["author"], ["title"], ["school"], ["year"]],
    "mastersthesis": [["author"], ["title"], ["school"], ["year"]],
    "techreport": [["author"], ["title"], ["institution"], ["year"]],
    "booklet": [["title"]],
    "manual": [["title"]],
    "unpublished": [["author"], ["title"], ["note"]],
    "misc": [],
}
DEFAULT_REQUIRED_GROUPS: List[List[str]] = ENTRY_REQUIRED_FIELD_GROUPS["misc"]
logger = logging.getLogger(__name__)


def _non_empty(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _required_whitelist(entry_type: str) -> List[str]:
    groups = ENTRY_REQUIRED_FIELD_GROUPS.get(entry_type, DEFAULT_REQUIRED_GROUPS)
    fields = []
    seen = set()
    for group in groups:
        for field in group:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    return fields


def sanitize_entry(entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Keep only required BibTeX keys and verify mandatory fields exist.
    Args:
        entry: Parsed BibTeX entry dictionary from bibtexparser.
    Returns:
        (sanitized_entry, error_message):
        - sanitized_entry is a cleaned entry dict when valid, otherwise None.
        - error_message is a short reason when invalid, otherwise None.
    """
    original_key = str(entry.get("ID", "")).strip()
    if not original_key:
        return None, "missing citation key (ID)"

    entry_type = str(entry.get("ENTRYTYPE", "misc")).strip().lower() or "misc"
    required_groups = ENTRY_REQUIRED_FIELD_GROUPS.get(entry_type, DEFAULT_REQUIRED_GROUPS)
    whitelist = _required_whitelist(entry_type)

    sanitized = {
        "ENTRYTYPE": entry_type,
        "ID": original_key,
    }

    for field in whitelist:
        value = entry.get(field)
        if _non_empty(value):
            sanitized[field] = value.strip()

    for group in required_groups:
        if any(_non_empty(sanitized.get(field)) for field in group):
            continue
        return None, f"missing required fields: one of {group}"

    return sanitized, None

def normalize_title(title: str) -> str:
    """
    Normalizes a title for deduplication:
    - Lowercase
    - Remove non-alphanumeric characters
    - Remove spaces
    """
    if not title:
        return ""
    # Remove LaTeX commands roughly if needed, but strict alphanumeric strip is usually good enough for fuzzy match
    s = title.lower()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def fingerprint_authors(author_str: str) -> str:
    """
    Generates a fingerprint for authors:
    - Split by 'and'
    - Extract last names
    - Sort and join with ':'
    """
    if not author_str:
        return ""

    # Clean up whitespace
    author_str = " ".join(author_str.split())

    # Split by ' and ' (case insensitive)
    authors = re.split(r'\s+and\s+', author_str, flags=re.IGNORECASE)
    last_names = []

    for a in authors:
        a = a.strip()
        if not a:
            continue

        # If contains comma, assume "Last, First"
        if ',' in a:
            last = a.split(',')[0].strip()
        else:
            # Assume "First Last" -> take last token
            # But handle cases like "Jean de La Fontaine" -> "Fontaine"?
            # Or "Van der Waals"?
            # For simple fingerprinting, taking the last token is a decent heuristic for 95% cases.
            tokens = a.split()
            if tokens:
                last = tokens[-1].strip()
            else:
                last = a

        # Remove non-alphanumeric from last name (e.g. accents converted or stripped)
        # We'll just lowercase and keep basic chars
        last = re.sub(r'[^a-zA-Z0-9]', '', last.lower())
        if last:
            last_names.append(last)

    last_names.sort()
    return ":".join(last_names)

def parse_bib_file(filepath: Path) -> List[Dict[str, Any]]:
    """
    Parses a bib file and returns a list of entries.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as bibtex_file:
            parser = BibTexParser(common_strings=True)
            # use convert_to_unicode to handle special latex chars
            parser.customization = convert_to_unicode
            parser.ignore_nonstandard_types = False
            bib_database = bibtexparser.load(bibtex_file, parser=parser)
        sanitized_entries: List[Dict[str, Any]] = []
        for entry in bib_database.entries:
            cleaned, err = sanitize_entry(entry)
            if cleaned is not None:
                sanitized_entries.append(cleaned)
            else:
                skipped_key = entry.get("ID", "(no ID)")
                logger.warning("Skip invalid entry '%s' in %s: %s", skipped_key, filepath.name, err)
        return sanitized_entries
    except Exception as e:
        logger.error("Error parsing %s: %s", filepath, e)
        return []

def get_entry_fingerprints(entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extracts fingerprints from an entry dict.
    """
    return {
        "original_key": entry.get("ID", ""),
        "normalized_title": normalize_title(entry.get("title", "")),
        "authors_fingerprint": fingerprint_authors(entry.get("author", "")),
        "year": entry.get("year", ""),
    }
