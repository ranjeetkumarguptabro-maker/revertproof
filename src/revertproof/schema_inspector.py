"""Live Database Schema Inspector & Diff Engine (PostgreSQL + SQLite).

Captures table/column/type/nullability snapshots before and after applying any SQL
migration so teams see the exact structural diff alongside compatibility results.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from revertproof.db import DatabaseAdapter


class ColumnMeta(BaseModel):
    name: str
    data_type: str
    nullable: bool = True


class SchemaSnapshot(BaseModel):
    tables: dict[str, dict[str, ColumnMeta]] = Field(default_factory=dict)


class ColumnChange(BaseModel):
    table: str
    column: str
    change_type: str  # ADDED, DROPPED, TYPE_CHANGED, NULLABILITY_CHANGED
    before: str | None = None
    after: str | None = None


class SchemaDiff(BaseModel):
    added_tables: list[str] = Field(default_factory=list)
    dropped_tables: list[str] = Field(default_factory=list)
    column_changes: list[ColumnChange] = Field(default_factory=list)

    @property
    def has_breaking_changes(self) -> bool:
        return bool(self.dropped_tables) or any(
            c.change_type in ("DROPPED", "TYPE_CHANGED") for c in self.column_changes
        )


def capture_schema_snapshot(db: DatabaseAdapter) -> SchemaSnapshot:
    """Inspect all tables and columns in the active isolated schema."""
    tables: dict[str, dict[str, ColumnMeta]] = {}
    if db.is_postgres:
        rows = db.fetchall(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = %s "
            "ORDER BY table_name, ordinal_position;",
            (db.schema_name,),
        )
        for r in rows:
            tname = str(r["table_name"]).lower()
            cname = str(r["column_name"]).lower()
            tables.setdefault(tname, {})[cname] = ColumnMeta(
                name=cname,
                data_type=str(r["data_type"]).upper(),
                nullable=str(r["is_nullable"]).upper() == "YES",
            )
    else:
        tbl_rows = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        for tr in tbl_rows:
            tname = str(tr["name"]).lower()
            col_rows = db.fetchall(f'PRAGMA table_info("{tname}");')
            cols: dict[str, ColumnMeta] = {}
            for cr in col_rows:
                cname = str(cr["name"]).lower()
                cols[cname] = ColumnMeta(
                    name=cname,
                    data_type=str(cr["type"] or "TEXT").upper(),
                    nullable=int(cr["notnull"]) == 0,
                )
            tables[tname] = cols

    return SchemaSnapshot(tables=tables)


def diff_schemas(before: SchemaSnapshot, after: SchemaSnapshot) -> SchemaDiff:
    """Compute structural schema diff between baseline and migrated database."""
    before_tables = set(before.tables.keys())
    after_tables = set(after.tables.keys())

    added_tables = sorted(after_tables - before_tables)
    dropped_tables = sorted(before_tables - after_tables)
    column_changes: list[ColumnChange] = []

    for tbl in sorted(before_tables & after_tables):
        b_cols = before.tables[tbl]
        a_cols = after.tables[tbl]

        for col in sorted(set(a_cols) - set(b_cols)):
            column_changes.append(
                ColumnChange(
                    table=tbl,
                    column=col,
                    change_type="ADDED",
                    after=a_cols[col].data_type,
                )
            )
        for col in sorted(set(b_cols) - set(a_cols)):
            column_changes.append(
                ColumnChange(
                    table=tbl,
                    column=col,
                    change_type="DROPPED",
                    before=b_cols[col].data_type,
                )
            )
        for col in sorted(set(b_cols) & set(a_cols)):
            bc, ac = b_cols[col], a_cols[col]
            if bc.data_type != ac.data_type:
                column_changes.append(
                    ColumnChange(
                        table=tbl,
                        column=col,
                        change_type="TYPE_CHANGED",
                        before=bc.data_type,
                        after=ac.data_type,
                    )
                )
            if bc.nullable != ac.nullable:
                column_changes.append(
                    ColumnChange(
                        table=tbl,
                        column=col,
                        change_type="NULLABILITY_CHANGED",
                        before="NULL" if bc.nullable else "NOT NULL",
                        after="NULL" if ac.nullable else "NOT NULL",
                    )
                )

    return SchemaDiff(
        added_tables=added_tables,
        dropped_tables=dropped_tables,
        column_changes=column_changes,
    )
