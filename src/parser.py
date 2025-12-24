import re
import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode
from pathlib import Path
from typing import List, Dict, Any

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
            parser = BibTexParser()
            # use convert_to_unicode to handle special latex chars
            parser.customization = convert_to_unicode
            bib_database = bibtexparser.load(bibtex_file, parser=parser)
        return bib_database.entries
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
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
