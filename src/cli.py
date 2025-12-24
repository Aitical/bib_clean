import typer
from rich.console import Console
from rich.table import Table
from typing import List, Optional
from pathlib import Path
import sqlite3
import json
from collections import defaultdict

# Use relative imports if running as package, but for direct execution we might need setup.
# Assuming we run as python -m src.cli
from src import db, parser, deduplicator, tex_updater

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
        # Check if any in this cluster are already processed (status != pending)
        # If so, maybe skip? For now, we revisit them.
        cursor.execute("SELECT * FROM entries WHERE cluster_id = ?", (cid,))
        rows = cursor.fetchall()

        # If all rows have same final_key and status!=pending, skip
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

            # Update Primary
            cursor.execute("UPDATE entries SET status='primary', final_key=? WHERE id=?", (primary_key, primary_id))

            # Update others as duplicate
            for row in rows:
                if row['id'] != primary_id:
                    cursor.execute("UPDATE entries SET status='duplicate', final_key=? WHERE id=?", (primary_key, row['id']))
            conn.commit()
            console.print(f"[green]Resolved using ID {primary_id} as primary.[/green]")
        elif choice == 's':
            console.print("Skipped (kept separate).")

    # 2. Handle Unique Pending
    # Set all pending unique entries to primary
    # Note: Only if cluster_id IS NULL.
    # If cluster_id was set but we skipped it ('s'), they remain pending?
    # If user chose 's', we should probably set them to 'primary' but keep their own keys?
    # For now, let's just auto-promote pending items that are NOT clustered.
    cursor.execute("UPDATE entries SET status='primary', final_key=original_key WHERE status='pending' AND cluster_id IS NULL")
    conn.commit()

    # Also promote items in clusters that were skipped?
    # If status is still pending, it means we skipped.
    # Let's promote them to primary with their own keys.
    cursor.execute("UPDATE entries SET status='primary', final_key=original_key WHERE status='pending'")
    conn.commit()

    # 3. Handle Collisions (Same Key, Different Content, Both Primary)
    # Get all primary keys that have count > 1
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
def export(output: Path):
    """
    Exports the merged and deduplicated BibTeX file.
    """
    conn = db.get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM entries WHERE status='primary'")
    rows = cursor.fetchall()

    try:
        with open(output, 'w', encoding='utf-8') as f:
            for row in rows:
                data = json.loads(row['raw_bib_json'])
                # Update ID to final_key
                data['ID'] = row['final_key']

                # Manual BibTeX dump
                entry_type = data.get('ENTRYTYPE', 'article')
                f.write(f"@{entry_type}{{{data['ID']},\n")

                # Sort keys for consistent output
                for k in sorted(data.keys()):
                    if k in ['ENTRYTYPE', 'ID']: continue
                    v = data[k]
                    # Escape braces if needed? Usually bibtexparser handles parsing,
                    # raw strings might be safe enough if they came from bibtexparser.
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

    # Build Key Map
    # Map OriginalKey -> FinalKey
    key_map = {}

    # Get all mappings where key changed
    cursor.execute("SELECT original_key, final_key FROM entries WHERE status='primary' OR status='duplicate'")
    all_maps = cursor.fetchall()

    # Detect ambiguities
    temp_map = defaultdict(set)
    for row in all_maps:
        if row['original_key'] != row['final_key']:
            temp_map[row['original_key']].add(row['final_key'])

    final_map = {}
    for k, v_set in temp_map.items():
        if len(v_set) == 1:
            final_map[k] = list(v_set)[0]
        else:
            console.print(f"[red]Warning: Key '{k}' maps to multiple final keys: {v_set}. Skipping update for this key to avoid incorrect citation.[/red]")

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
