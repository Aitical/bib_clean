import typer
from rich.console import Console
from rich.table import Table
from typing import List, Optional, Dict
from pathlib import Path
import sqlite3
import json
import csv
from collections import defaultdict
import os

from src import db, parser, deduplicator, tex_updater, matcher

app = typer.Typer()
console = Console()

@app.command()
def add(files: List[Path]):
    """
    Parses bib files and adds them to the database.
    """
    conn = db.get_connection()
    db.init_db(conn)

    for p in files:
        if not p.exists():
            console.print(f"[red]File not found: {p}[/red]")
            continue

        console.print(f"Parsing {p}...")
        source_id = db.add_source(conn, str(p.absolute()))
        entries = parser.parse_bib_file(p)

        count = 0
        for entry in entries:
            fingerprints = parser.get_entry_fingerprints(entry)
            db.add_entry(
                conn,
                source_id,
                fingerprints['original_key'],
                fingerprints['normalized_title'],
                fingerprints['authors_fingerprint'],
                fingerprints['year'],
                entry
            )
            count += 1
        console.print(f"Added {count} entries from {p.name}")

    conn.close()

@app.command()
def scan():
    """
    Scans the database for duplicates and reports statistics.
    """
    conn = db.get_connection()
    try:
        stats = deduplicator.scan_and_cluster(conn)
        console.print(f"[green]Scan complete.[/green]")
        console.print(f"Found {stats['clusters_found']} clusters with potential duplicates.")
        console.print(f"Total {stats['duplicates_found']} duplicate entries identified.")

        # Check collisions
        collisions = deduplicator.get_dangerous_collisions(conn)
        if collisions:
            console.print(f"[yellow]Found {len(collisions)} keys with collisions (Same Key, Different Paper).[/yellow]")
            console.print("Run 'resolve' to fix specific collisions.")
    except Exception as e:
        console.print(f"[red]Error during scan: {e}[/red]")
    finally:
        conn.close()

@app.command()
def resolve():
    """
    Interactive tool to resolve duplicates and collisions.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    # 1. Handle Duplicates (Clusters)
    cursor.execute("SELECT DISTINCT cluster_id FROM entries WHERE cluster_id IS NOT NULL")
    clusters = [r[0] for r in cursor.fetchall()]

    for cid in clusters:
        cursor.execute("SELECT * FROM entries WHERE cluster_id = ?", (cid,))
        rows = cursor.fetchall()

        if all(row['status'] != 'pending' for row in rows):
            continue

        table = Table(title=f"Duplicate Cluster {cid}")
        table.add_column("ID", justify="right", style="cyan")
        table.add_column("Key", style="magenta")
        table.add_column("Title")
        table.add_column("Authors")
        table.add_column("Year")
        table.add_column("Source")

        options = {}
        first_id = str(rows[0]['id'])

        for row in rows:
            cursor.execute("SELECT filepath FROM sources WHERE id = ?", (row['source_id'],))
            source_res = cursor.fetchone()
            source_path = Path(source_res[0]).name if source_res else "Unknown"

            table.add_row(
                str(row['id']),
                row['original_key'],
                row['normalized_title'][:50],
                row['authors_fingerprint'][:30] if row['authors_fingerprint'] else "",
                row['year'] or "",
                source_path
            )
            options[str(row['id'])] = row

        console.print(table)

        choice = typer.prompt(
            "Select Primary ID to keep (or 's' to skip/keep separate, 'a' for auto-pick first)",
            default="a"
        )

        if choice == 'a':
            choice = first_id

        if choice in options:
            primary_id = int(choice)
            primary_row = options[choice]
            primary_key = primary_row['original_key']

            cursor.execute("UPDATE entries SET status='primary', final_key=? WHERE id=?", (primary_key, primary_id))
            for row in rows:
                if row['id'] != primary_id:
                    cursor.execute("UPDATE entries SET status='duplicate', final_key=? WHERE id=?", (primary_key, row['id']))
            conn.commit()
            console.print(f"[green]Resolved using ID {primary_id} as primary.[/green]")
        elif choice == 's':
            console.print("Skipped (kept separate).")

    # 2. Handle Unique Pending
    cursor.execute("UPDATE entries SET status='primary', final_key=original_key WHERE status='pending' AND cluster_id IS NULL")
    conn.commit()
    cursor.execute("UPDATE entries SET status='primary', final_key=original_key WHERE status='pending'")
    conn.commit()

    # 3. Handle Collisions
    cursor.execute("""
    SELECT final_key, COUNT(*) as c
    FROM entries
    WHERE status='primary'
    GROUP BY final_key
    HAVING c > 1
    """)
    collisions = cursor.fetchall()

    for row in collisions:
        key = row['final_key']
        console.print(f"\n[red]Collision detected for key: {key}[/red]")
        cursor.execute("SELECT * FROM entries WHERE status='primary' AND final_key=?", (key,))
        conflicts = cursor.fetchall()

        for c_row in conflicts:
            cursor.execute("SELECT filepath FROM sources WHERE id = ?", (c_row['source_id'],))
            src = Path(cursor.fetchone()[0]).name
            console.print(f"ID: {c_row['id']} | Title: {c_row['normalized_title'][:50]} | Source: {src}")

            new_key = typer.prompt(f"Enter new key for ID {c_row['id']}", default=f"{key}_{c_row['id']}")
            cursor.execute("UPDATE entries SET final_key=? WHERE id=?", (new_key, c_row['id']))
        conn.commit()

    conn.close()

@app.command()
def normalize(
    csv_path: Optional[Path] = typer.Option(None, "--csv-path", "-c", help="Path to CSV with canonical strings (cols: abbr, name)"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="OpenAI API Key (or set OPENAI_API_KEY env)"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="OpenAI Base URL (or set OPENAI_BASE_URL env)"),
    model: str = typer.Option("gpt-3.5-turbo", "--model", help="LLM Model to use"),
    review: bool = typer.Option(False, "--review", help="Interactive review of LLM matches")
):
    """
    Normalizes journal/conference names to canonical keys using Fuzzy Matching + LLM.
    """
    conn = db.get_connection()
    db.init_db(conn)

    # 1. Import CSV
    if csv_path and csv_path.exists():
        console.print(f"Importing canonical strings from {csv_path}...")
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                count = 0
                for row in reader:
                    # Expect 'abbr' and 'name'
                    key = row.get('abbr') or row.get('key')
                    name = row.get('name') or row.get('value')

                    if key and name:
                        db.upsert_canonical_string(conn, key.strip(), name.strip())
                        # Also add exact match
                        db.upsert_string_mapping(conn, name.strip(), key.strip(), 'csv_exact', 1.0, 1)
                        db.upsert_string_mapping(conn, key.strip(), key.strip(), 'csv_exact', 1.0, 1)
                        count += 1
            console.print(f"[green]Imported {count} canonical strings.[/green]")
        except Exception as e:
            console.print(f"[red]Error importing CSV: {e}[/red]")

    # 2. Run Matching
    f_matcher = matcher.FuzzyMatcher(conn, api_key=api_key, base_url=base_url, model=model)

    console.print("Scanning entries for unmapped strings...")
    cursor = conn.cursor()
    cursor.execute("SELECT raw_bib_json FROM entries")
    rows = cursor.fetchall()

    unique_strings = set()
    for row in rows:
        data = json.loads(row['raw_bib_json'])
        for field in ['journal', 'booktitle', 'publisher']:
            val = data.get(field)
            if val and isinstance(val, str):
                unique_strings.add(val)

    # Filter out existing mappings
    existing_maps = db.get_all_string_mappings(conn)
    to_process = [s for s in unique_strings if s not in existing_maps]

    console.print(f"Found {len(to_process)} unmapped strings.")

    new_maps = 0
    if to_process:
        with typer.progressbar(to_process, label="Matching") as progress:
            for s in progress:
                res = f_matcher.match(s)
                if res.key:
                    db.upsert_string_mapping(
                        conn,
                        s,
                        res.key,
                        res.source,
                        res.confidence,
                        1 if res.source == 'exact' else 0
                    )
                    new_maps += 1

    console.print(f"[green]Normalization loop complete. {new_maps} new mappings found.[/green]")

    # 3. Review
    if review:
        cursor.execute("SELECT * FROM string_mappings WHERE is_verified = 0")
        pending = cursor.fetchall()

        if not pending:
            console.print("No pending mappings to review.")
        else:
            console.print(f"Reviewing {len(pending)} mappings...")
            for row in pending:
                orig = row['original_string']
                mapped = row['mapped_key']
                source = row['source']
                conf = row['confidence']

                choice = typer.prompt(
                    f"Map '{orig}' -> '{mapped}' ({source}, {conf})? [y/n/skip]",
                    default="y"
                )

                if choice.lower() == 'y':
                    cursor.execute("UPDATE string_mappings SET is_verified = 1 WHERE original_string = ?", (orig,))
                elif choice.lower() == 'n':
                    cursor.execute("DELETE FROM string_mappings WHERE original_string = ?", (orig,))
                    console.print("Mapping removed.")
            conn.commit()

    conn.close()

@app.command()
def export(output: Path):
    """
    Exports the merged and deduplicated BibTeX file.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    # Load Normalization Maps
    string_defs = db.get_canonical_strings(conn)
    string_maps = db.get_all_string_mappings(conn)

    cursor.execute("SELECT * FROM entries WHERE status='primary'")
    rows = cursor.fetchall()

    try:
        with open(output, 'w', encoding='utf-8') as f:
            f.write("% String Definitions\n")
            for k in sorted(string_defs.keys()):
                val = string_defs[k]
                f.write(f'@String{{{k} = "{val}"}}\n')
            f.write("\n")

            for row in rows:
                data = json.loads(row['raw_bib_json'])
                data['ID'] = row['final_key']

                entry_type = data.get('ENTRYTYPE', 'article')
                f.write(f"@{entry_type}{{{data['ID']},\n")

                for k in sorted(data.keys()):
                    if k in ['ENTRYTYPE', 'ID']: continue
                    v = data[k]

                    # Check mapping
                    # Ensure v is string and map has it
                    if isinstance(v, str) and v in string_maps:
                        mapped_key = string_maps[v]
                        f.write(f"  {k} = {mapped_key},\n")
                    else:
                        f.write(f"  {k} = {{{v}}},\n")
                f.write("}\n\n")

        console.print(f"[green]Exported {len(rows)} entries to {output}[/green]")
    except Exception as e:
        console.print(f"[red]Error exporting: {e}[/red]")
    finally:
        conn.close()

@app.command()
def update_tex(tex_path: Path):
    """
    Updates TeX files with the new citation keys.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    key_map = {}
    cursor.execute("SELECT original_key, final_key FROM entries WHERE status='primary' OR status='duplicate'")
    all_maps = cursor.fetchall()

    temp_map = defaultdict(set)
    for row in all_maps:
        if row['original_key'] != row['final_key']:
            temp_map[row['original_key']].add(row['final_key'])

    final_map = {}
    for k, v_set in temp_map.items():
        if len(v_set) == 1:
            final_map[k] = list(v_set)[0]
        else:
            console.print(f"[red]Warning: Key '{k}' maps to multiple final keys: {v_set}. Skipping update.[/red]")

    if not final_map:
        console.print("No key changes needed.")
        return

    console.print(f"Applying {len(final_map)} key replacements...")

    if tex_path.is_file():
        tex_updater.process_tex_file(tex_path, final_map)
    elif tex_path.is_dir():
        for p in tex_path.glob("**/*.tex"):
            tex_updater.process_tex_file(p, final_map)
    else:
        console.print(f"[red]Invalid path: {tex_path}[/red]")

    conn.close()

if __name__ == "__main__":
    app()
