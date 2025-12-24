import re
import shutil
from pathlib import Path
from typing import Dict, Tuple

def find_comment_start(line: str) -> int:
    """
    Finds the index of the first unescaped % character.
    Returns -1 if no comment.
    """
    idx = 0
    while True:
        idx = line.find('%', idx)
        if idx == -1:
            return -1

        # Check if escaped
        # Count backslashes before idx
        backslashes = 0
        i = idx - 1
        while i >= 0 and line[i] == '\\':
            backslashes += 1
            i -= 1

        if backslashes % 2 == 0:
            # Even number of backslashes means % is NOT escaped (or escaped backslash before it)
            return idx

        # Was escaped, continue search
        idx += 1

def process_line(line: str, key_map: Dict[str, str]) -> str:
    """
    Replaces keys in a single line, respecting comments.
    """
    comment_idx = find_comment_start(line)

    if comment_idx != -1:
        code_part = line[:comment_idx]
        comment_part = line[comment_idx:]
    else:
        code_part = line
        comment_part = ""

    # Replace in code_part
    # Regex for citation keys
    # Supports \cite{a,b}, \citep[p.1]{a}, etc.
    # Group 1: command + options + {
    # Group 2: keys
    # Group 3: }
    # Added more citation commands common in biblatex/natbib
    pattern = r'(\\(?:cite|citep|citet|nocite|bibentry|citeauthor|citeyear|footcite|textcite|parencite)(?:\[.*?\])?\{)(.+?)(\})'

    def replace_match(match):
        prefix = match.group(1)
        keys_str = match.group(2)
        suffix = match.group(3)

        keys = [k.strip() for k in keys_str.split(',')]
        new_keys = []
        for k in keys:
            # If mapped, use new key. Else keep old key.
            # Warning: If key is not in map, it might be unmanaged or correct. We leave it.
            new_keys.append(key_map.get(k, k))

        return f"{prefix}{','.join(new_keys)}{suffix}"

    new_code_part = re.sub(pattern, replace_match, code_part)

    return new_code_part + comment_part

def process_tex_file(filepath: Path, key_map: Dict[str, str], backup: bool = True):
    """
    Reads file, replaces keys, writes back.
    """
    path = Path(filepath)
    if not path.exists():
        print(f"File not found: {path}")
        return

    if backup:
        backup_path = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup_path)
        print(f"Backup created: {backup_path}")

    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        # Fallback to latin-1 if utf-8 fails
        with open(path, 'r', encoding='latin-1') as f:
            lines = f.readlines()

    new_lines = [process_line(line, key_map) for line in lines]

    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"Updated {path}")
