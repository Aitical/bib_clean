import sqlite3
from typing import Dict, List

def scan_and_cluster(conn: sqlite3.Connection) -> Dict[str, int]:
    """
    Groups entries by fingerprint and updates cluster_id.
    Returns stats: {'clusters_found': int, 'duplicates_found': int}
    """
    cursor = conn.cursor()

    # Reset clusters
    cursor.execute("UPDATE entries SET cluster_id = NULL")

    # Find potential duplicates
    # Group by title + authors
    query = """
    SELECT normalized_title, authors_fingerprint, COUNT(*) as count
    FROM entries
    WHERE normalized_title != ''
    GROUP BY normalized_title, authors_fingerprint
    HAVING count > 1
    """

    cursor.execute(query)
    groups = cursor.fetchall()

    cluster_count = 0
    duplicate_count = 0

    for group in groups:
        cluster_count += 1
        title = group['normalized_title']
        authors = group['authors_fingerprint']
        count = group['count']
        duplicate_count += (count - 1)

        # Update these entries with a new cluster_id
        # We need to handle NULL authors explicitly if they exist
        if authors is None:
             update_query = "UPDATE entries SET cluster_id = ? WHERE normalized_title = ? AND authors_fingerprint IS NULL"
             cursor.execute(update_query, (cluster_count, title))
        else:
             update_query = "UPDATE entries SET cluster_id = ? WHERE normalized_title = ? AND authors_fingerprint = ?"
             cursor.execute(update_query, (cluster_count, title, authors))

    conn.commit()
    return {'clusters_found': cluster_count, 'duplicates_found': duplicate_count}

def get_dangerous_collisions(conn: sqlite3.Connection) -> List[str]:
    """
    Finds original keys that are used by DIFFERENT papers (different normalized titles).
    These MUST be renamed.
    """
    cursor = conn.cursor()

    # We assume 'Different Paper' means Different Normalized Title.
    # (Authors could vary slightly for same paper, but Title is stronger)
    query = """
    SELECT original_key
    FROM entries
    GROUP BY original_key
    HAVING COUNT(DISTINCT normalized_title) > 1
    """

    cursor.execute(query)
    return [row['original_key'] for row in cursor.fetchall()]
