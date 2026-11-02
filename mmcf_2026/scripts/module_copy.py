"""
Copies every row tied to a set of `data_id`s from a source CANOE (v4) database
into a destination CANOE database with the same schema.

The `data_set` table is the canonical registry of `data_id` values: every other
table that carries a `data_id` column has a foreign key back to it. This script
finds the `data_id`s in `data_set` matching a pattern (default: starting with
"TRP"), then copies matching rows from every table that has a `data_id` column.

A data_id-only copy can still leave a table referencing a `commodity` or
`technology` that was defined under some *other* data_id (e.g. a shared entity
maintained by a different module) and therefore never gets pulled across. To
cover that, every commodity/technology name matching any of a separate set of
patterns (default: starting with "T_" or "F_T_") is identified, and then every
row in every table that references one of those names - by any column FK'd to
`commodity_label`/`technology_label` (e.g. `efficiency`, `cost_fixed`,
`capacity_factor_tech`, the `commodity`/`technology` definition itself, ...) -
is copied too, regardless of that row's own data_id.

Every data_id ends in a 3-digit version (e.g. "TRPHRSK002"). A version is
required from the caller and is stamped onto *every* data_id this script
touches - matched, entity-referenced, or a data_set.parent_id ancestor of
either - so the whole imported slice (including borrowed shared entities and
their datasets) ends up self-consistent under the new version instead of a
mix of old and new. A data_set row for a referenced/ancestor data_id is
created under the new version only if one doesn't already exist there.
"""

import argparse
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from canoe_schema.sql import get_sql_schema
from loguru import logger

# (label_table, label_column, source_table, source_column)
DEFAULT_LABEL_SPECS = (
    ("commodity_label", "commodity", "commodity", "name"),
    ("technology_label", "tech", "technology", "tech"),
    ("tech_group_label", "group_name", "tech_group", "group_name"),
    ("sector_label", "sector", "technology", "sector"),
    ("data_source_label", "source_id", "data_source", "source_id"),
)
DEFAULT_DATASET_TABLE = "data_set"
DEFAULT_PATTERN = "TRP%"
DEFAULT_ENTITY_PATTERNS = ("T_*", "F_T_*")
SCHEMA_VERSION = "4.0"

# Label tables (e.g. commodity_label, technology_label) carry no data_id of their
# own and are shared across data_ids, so they're copied wholesale from source
# rather than filtered/derived like the data_id-scoped tables.
LABEL_TABLES = tuple(label_table for label_table, *_ in DEFAULT_LABEL_SPECS)

# Label tables that anchor the entity-pattern sweep: any table with a column
# FK'd to one of these is considered to "reference" a commodity/technology.
ENTITY_LABEL_TABLES = (
    ("technology_label", "tech"),
    ("commodity_label", "commodity"),
)

# SQLite caps the number of bound parameters per statement (historically 999).
_SQLITE_MAX_VARIABLES = 999


def prepare_destination(db_path: Path) -> None:
    """Creates an empty destination database with the CANOE v4 schema if needed."""
    if db_path.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(get_sql_schema(SCHEMA_VERSION))
    logger.info(f"Created new destination database at {db_path}")


def get_data_id_tables(
    conn: sqlite3.Connection, dataset_table: str = DEFAULT_DATASET_TABLE
) -> list[str]:
    """Returns every table with a `data_id` column, `dataset_table` listed first
    so it is copied before the tables that reference it."""
    tables = [
        t[0]
        for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    data_id_tables = [
        t
        for t in tables
        if "data_id" in {c[1] for c in conn.execute(f'PRAGMA table_info("{t}");')}
    ]
    data_id_tables.sort(key=lambda t: t != dataset_table)
    return data_id_tables


def get_matching_data_ids(
    conn: sqlite3.Connection, pattern: str, dataset_table: str = DEFAULT_DATASET_TABLE
) -> list[str]:
    """Returns the `data_id`s in `dataset_table` matching `pattern` (SQL LIKE syntax)."""
    rows = conn.execute(
        f'SELECT data_id FROM "{dataset_table}" WHERE data_id LIKE ?', (pattern,)
    ).fetchall()
    return [r[0] for r in rows]


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def copy_table(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    table: str,
    data_ids: list[str],
) -> int:
    """Copies rows from `table` in `source_conn` matching `data_ids` into `dest_conn`."""
    columns = [c[1] for c in source_conn.execute(f'PRAGMA table_info("{table}");')]
    col_sql = ", ".join(f'"{c}"' for c in columns)

    copied = 0
    for batch in chunked(data_ids, _SQLITE_MAX_VARIABLES):
        placeholders = ", ".join("?" for _ in batch)
        rows = source_conn.execute(
            f'SELECT {col_sql} FROM "{table}" WHERE data_id IN ({placeholders})', batch
        ).fetchall()
        if not rows:
            continue
        value_placeholders = ", ".join("?" for _ in columns)
        dest_conn.executemany(
            f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({value_placeholders})',
            rows,
        )
        copied += len(rows)
    return copied


def make_versioned_data_id(data_id: str, version: str) -> str:
    """Replaces the trailing 3-digit version suffix of `data_id` with `version`."""
    if len(version) != 3 or not version.isdigit():
        raise ValueError(f"version must be exactly 3 digits, got {version!r}")
    if len(data_id) < 3 or not data_id[-3:].isdigit():
        raise ValueError(f"data_id {data_id!r} does not end with a 3-digit version")
    return data_id[:-3] + version


def copy_table_versioned(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    table: str,
    data_ids: list[str],
    version_map: dict[str, str],
) -> int:
    """Like copy_table, but rewrites each row's data_id (and parent_id, if the
    table has one) through `version_map` so the imported rows land under the
    new versioned data_id instead of the source's original one."""
    columns = [c[1] for c in source_conn.execute(f'PRAGMA table_info("{table}");')]
    col_sql = ", ".join(f'"{c}"' for c in columns)
    data_id_idx = columns.index("data_id")
    parent_id_idx = columns.index("parent_id") if "parent_id" in columns else None

    copied = 0
    for batch in chunked(data_ids, _SQLITE_MAX_VARIABLES):
        placeholders = ", ".join("?" for _ in batch)
        rows = source_conn.execute(
            f'SELECT {col_sql} FROM "{table}" WHERE data_id IN ({placeholders})', batch
        ).fetchall()
        if not rows:
            continue
        versioned_rows = []
        for row in rows:
            row = list(row)
            row[data_id_idx] = version_map[row[data_id_idx]]
            if parent_id_idx is not None:
                row[parent_id_idx] = version_map.get(
                    row[parent_id_idx], row[parent_id_idx]
                )
            versioned_rows.append(tuple(row))
        value_placeholders = ", ".join("?" for _ in columns)
        dest_conn.executemany(
            f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({value_placeholders})',
            versioned_rows,
        )
        copied += len(versioned_rows)
    return copied


def copy_label_table(
    source_conn: sqlite3.Connection, dest_conn: sqlite3.Connection, table: str
) -> int:
    """Copies a global label table (no data_id) wholesale from source to dest."""
    columns = [c[1] for c in source_conn.execute(f'PRAGMA table_info("{table}");')]
    col_sql = ", ".join(f'"{c}"' for c in columns)

    rows = source_conn.execute(f'SELECT {col_sql} FROM "{table}"').fetchall()
    if not rows:
        return 0
    placeholders = ", ".join("?" for _ in columns)
    dest_conn.executemany(
        f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({placeholders})', rows
    )
    return len(rows)


def resolve_data_id_ancestors(
    source_conn: sqlite3.Connection,
    data_ids: set[str],
    dataset_table: str = DEFAULT_DATASET_TABLE,
) -> set[str]:
    """Follows data_set.parent_id upward from `data_ids` and returns the full
    ancestor closure (including the input ids). A dataset can derive from a
    parent dataset, and only the parent's data_set row is needed here (not its
    full data footprint), or the child's own parent_id FK would be left dangling."""
    all_ids = set(data_ids)
    frontier = set(data_ids)
    while frontier:
        placeholders = ", ".join("?" for _ in frontier)
        rows = source_conn.execute(
            f'SELECT DISTINCT parent_id FROM "{dataset_table}" '
            f"WHERE data_id IN ({placeholders}) AND parent_id IS NOT NULL",
            list(frontier),
        ).fetchall()
        frontier = {r[0] for r in rows} - all_ids
        all_ids |= frontier
    return all_ids


def get_matched_entity_names(
    source_conn: sqlite3.Connection,
    label_table: str,
    label_column: str,
    patterns: Sequence[str],
) -> set[str]:
    """Returns the names in `label_table` matching any of `patterns` (GLOB).
    Querying the label table (not any one data table) captures the full
    universe of valid names regardless of which data_id defines them."""
    names: set[str] = set()
    for pattern in patterns:
        rows = source_conn.execute(
            f'SELECT DISTINCT "{label_column}" FROM "{label_table}" '
            f'WHERE "{label_column}" GLOB ?',
            (pattern,),
        ).fetchall()
        names.update(r[0] for r in rows)
    return names


def discover_referencing_tables(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, list[str]]]:
    """Returns, for every table with at least one column FK'd to
    `technology_label` or `commodity_label`, those referencing columns per
    parent label table: {table: {"technology_label": [cols], "commodity_label": [cols]}}."""
    tables = [
        t[0]
        for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        ).fetchall()
    ]
    parent_tables = [label_table for label_table, _ in ENTITY_LABEL_TABLES]
    result: dict[str, dict[str, list[str]]] = {}
    for table in tables:
        fk_list = conn.execute(f'PRAGMA foreign_key_list("{table}");').fetchall()
        cols_by_parent = {
            parent: [row[3] for row in fk_list if row[2] == parent]
            for parent in parent_tables
        }
        if any(cols_by_parent.values()):
            result[table] = cols_by_parent
    return result


def _entity_match_where(
    cols_by_parent: dict[str, list[str]],
    matched_names: dict[str, set[str]],
) -> tuple[str, list[str]] | None:
    """Builds a 'col1 IN (...) OR col2 IN (...) OR ...' fragment matching any
    referencing column against its parent label's matched-name set. Returns
    None if there's nothing to match."""
    clauses = []
    params: list[str] = []
    for parent, cols in cols_by_parent.items():
        names = matched_names.get(parent)
        if not names:
            continue
        placeholders = ", ".join("?" for _ in names)
        for col in cols:
            clauses.append(f'"{col}" IN ({placeholders})')
            params.extend(names)
    if not clauses:
        return None
    return " OR ".join(clauses), params


def get_referencing_data_ids(
    source_conn: sqlite3.Connection,
    table: str,
    cols_by_parent: dict[str, list[str]],
    matched_names: dict[str, set[str]],
) -> list[str]:
    """Returns the distinct data_ids of rows in `table` that reference a
    matched entity name, if `table` has a data_id column."""
    if "data_id" not in {
        c[1] for c in source_conn.execute(f'PRAGMA table_info("{table}");')
    }:
        return []
    where = _entity_match_where(cols_by_parent, matched_names)
    if where is None:
        return []
    where_sql, params = where
    rows = source_conn.execute(
        f'SELECT DISTINCT data_id FROM "{table}" '
        f"WHERE ({where_sql}) AND data_id IS NOT NULL",
        params,
    ).fetchall()
    return [r[0] for r in rows]


def copy_referencing_rows(
    source_conn: sqlite3.Connection,
    dest_conn: sqlite3.Connection,
    table: str,
    cols_by_parent: dict[str, list[str]],
    matched_names: dict[str, set[str]],
    version_map: dict[str, str],
    exclude_data_ids: set[str] = frozenset(),
) -> int:
    """Copies every row in `table` that references a matched entity name
    (via any column FK'd to technology_label/commodity_label), regardless of
    the row's own data_id. If `table` has a data_id (and parent_id, for
    data_set) column, it's rewritten through `version_map` so a borrowed row
    lands under the new version too, not the source's original one.

    `exclude_data_ids` should be the data_ids already handled by the versioned
    data_id-scoped copy, so a row isn't also re-copied here."""
    where = _entity_match_where(cols_by_parent, matched_names)
    if where is None:
        return 0
    where_sql, params = where

    columns = [c[1] for c in source_conn.execute(f'PRAGMA table_info("{table}");')]
    col_sql = ", ".join(f'"{c}"' for c in columns)
    data_id_idx = columns.index("data_id") if "data_id" in columns else None
    parent_id_idx = columns.index("parent_id") if "parent_id" in columns else None

    query = f'SELECT {col_sql} FROM "{table}" WHERE ({where_sql})'
    if data_id_idx is not None and exclude_data_ids:
        placeholders = ", ".join("?" for _ in exclude_data_ids)
        query += f" AND (data_id IS NULL OR data_id NOT IN ({placeholders}))"
        params = params + list(exclude_data_ids)

    rows = source_conn.execute(query, params).fetchall()
    if not rows:
        return 0

    versioned_rows = []
    for row in rows:
        row = list(row)
        if data_id_idx is not None and row[data_id_idx] is not None:
            row[data_id_idx] = version_map[row[data_id_idx]]
        if parent_id_idx is not None and row[parent_id_idx] is not None:
            row[parent_id_idx] = version_map.get(
                row[parent_id_idx], row[parent_id_idx]
            )
        versioned_rows.append(tuple(row))

    placeholders = ", ".join("?" for _ in columns)
    dest_conn.executemany(
        f'INSERT OR IGNORE INTO "{table}" ({col_sql}) VALUES ({placeholders})',
        versioned_rows,
    )
    return len(versioned_rows)


def run(
    source_path: str,
    dest_path: str,
    pattern: str = DEFAULT_PATTERN,
    dataset_table: str = DEFAULT_DATASET_TABLE,
    entity_patterns: Sequence[str] = DEFAULT_ENTITY_PATTERNS,
    *,
    version: str,
) -> None:
    if len(version) != 3 or not version.isdigit():
        raise ValueError(f"version must be exactly 3 digits, got {version!r}")

    dest_path_obj = Path(dest_path)
    prepare_destination(dest_path_obj)

    source_conn = sqlite3.connect(source_path)
    dest_conn = sqlite3.connect(dest_path_obj)
    dest_conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        data_ids = get_matching_data_ids(source_conn, pattern, dataset_table)
        if not data_ids:
            logger.warning(
                f"No data_ids in {dataset_table} matched pattern {pattern!r}"
            )
            return
        logger.info(f"Found {len(data_ids)} matching data_id(s): {data_ids}")

        # Every table with a column FK'd to technology_label/commodity_label
        # "references" technologies/commodities and is swept in below (e.g.
        # efficiency, cost_fixed, capacity_factor_tech, the commodity/technology
        # definitions themselves, ...).
        referencing_tables = discover_referencing_tables(source_conn)
        matched_names = {
            label_table: get_matched_entity_names(
                source_conn, label_table, label_column, entity_patterns
            )
            for label_table, label_column in ENTITY_LABEL_TABLES
        }
        if entity_patterns:
            logger.info(
                f"Entity pattern(s) {list(entity_patterns)!r} matched "
                + ", ".join(
                    f"{len(names)} {label_table}" for label_table, names in matched_names.items()
                )
            )

        # Any row anywhere referencing a matched entity is swept in regardless
        # of its own data_id (e.g. the entity's own definition/efficiency/cost
        # rows, maintained by a different module).
        entity_data_ids: set[str] = set()
        if entity_patterns:
            for table, cols_by_parent in referencing_tables.items():
                entity_data_ids.update(
                    get_referencing_data_ids(
                        source_conn, table, cols_by_parent, matched_names
                    )
                )

        # Every data_id we depend on - matched, entity-referenced, or an ancestor
        # of either via data_set.parent_id - gets stamped with the same version,
        # so the whole imported slice (including borrowed shared entities) ends
        # up self-consistent under the new version, not a mix of old and new.
        all_data_ids = resolve_data_id_ancestors(
            source_conn, set(data_ids) | entity_data_ids, dataset_table
        )
        version_map = {d: make_versioned_data_id(d, version) for d in all_data_ids}
        logger.info(f"Stamping {len(version_map)} data_id(s) with version {version!r}")

        tables = get_data_id_tables(source_conn, dataset_table)
        for table in tables:
            copied = copy_table_versioned(
                source_conn, dest_conn, table, data_ids, version_map
            )
            if copied:
                logger.info(f"Copied {copied} row(s) into {table}")

        # Entity-referenced/ancestor data_ids aren't in `data_ids` (no full data
        # copy), but still need a data_set row under the new version - created
        # only if one doesn't already exist there (INSERT OR IGNORE).
        dataset_only_ids = sorted(all_data_ids - set(data_ids))
        if dataset_only_ids:
            copied = copy_table_versioned(
                source_conn, dest_conn, dataset_table, dataset_only_ids, version_map
            )
            if copied:
                logger.info(
                    f"Copied {copied} row(s) into {dataset_table} for "
                    f"referenced/ancestor data_id(s) {dataset_only_ids}"
                )

        if entity_patterns:
            for table, cols_by_parent in referencing_tables.items():
                copied = copy_referencing_rows(
                    source_conn,
                    dest_conn,
                    table,
                    cols_by_parent,
                    matched_names,
                    version_map,
                    exclude_data_ids=set(data_ids),
                )
                if copied:
                    logger.info(
                        f"Copied {copied} row(s) into {table} referencing "
                        "matched entities (any data_id)"
                    )

        for table in LABEL_TABLES:
            copied = copy_label_table(source_conn, dest_conn, table)
            if copied:
                logger.info(f"Copied {copied} row(s) into {table}")

        dest_conn.execute("PRAGMA foreign_keys = ON;")
        fk_errors = dest_conn.execute("PRAGMA foreign_key_check;").fetchall()
        if fk_errors:
            logger.error(f"Foreign key check failed after copy: {fk_errors}")
            dest_conn.rollback()
            raise RuntimeError("Foreign key check failed after copy")

        dest_conn.commit()
        logger.success(
            f"Copied {len(data_ids)} data_id(s) into {dest_path} as "
            f"{sorted(version_map[d] for d in data_ids)}"
        )
    finally:
        source_conn.close()
        dest_conn.close()


def main(argv: list[str] | None = None) -> None:
    """argv=None → reads sys.argv (standalone mode). argv=[...] → used by Typer wrapper."""
    parser = argparse.ArgumentParser(
        prog="copy-trp",
        description=(
            "Copy all rows tied to data_ids matching a pattern from a source "
            "CANOE v4 database into a destination CANOE v4 database."
        ),
        epilog=(
            "Example:\n"
            "  copy_trp.py source.sqlite dest.sqlite --pattern 'TRP%' --version 005"
            " --entity-pattern 'T_*' 'F_T_*'"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Path to the source database")
    parser.add_argument("destination", help="Path to the destination database")
    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
        help=(
            "SQL LIKE pattern to match data_ids against "
            f"(default: {DEFAULT_PATTERN!r})".replace("%", "%%")
        ),
    )
    parser.add_argument("--dataset-table", default=DEFAULT_DATASET_TABLE)
    parser.add_argument(
        "--entity-pattern",
        dest="entity_patterns",
        nargs="*",
        default=list(DEFAULT_ENTITY_PATTERNS),
        help=(
            "SQL GLOB pattern(s) to match commodity/technology names against, "
            "copied regardless of data_id (default: "
            f"{list(DEFAULT_ENTITY_PATTERNS)!r}). Space-separated for "
            "multiple, e.g. --entity-pattern 'T_*' 'F_T_*'. Pass with no "
            "values to disable."
        ),
    )
    parser.add_argument(
        "--version",
        required=True,
        help=(
            "3-digit version to stamp onto every matched data_id's trailing "
            "version suffix (e.g. '005' turns 'TRPHRSK002' into 'TRPHRSK005')."
        ),
    )
    args = parser.parse_args(argv)

    run(
        args.source,
        args.destination,
        args.pattern,
        args.dataset_table,
        args.entity_patterns,
        version=args.version,
    )


if __name__ == "__main__":
    main()
