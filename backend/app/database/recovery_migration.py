"""Explicit, additive migration marker; old user tables are never rebuilt."""

from sqlalchemy import text


def migrate_recovery(connection):
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS studio_schema_migrations "
            "(component VARCHAR(40) PRIMARY KEY, version INTEGER NOT NULL)"
        )
    )
    value = connection.execute(
        text("SELECT version FROM studio_schema_migrations WHERE component='agent_recovery'")
    ).scalar_one_or_none()
    if value is not None and value > 1:
        raise ValueError("Recovery database version is newer than this application")
    connection.execute(
        text(
            "INSERT OR IGNORE INTO studio_schema_migrations(component,version) VALUES('agent_recovery',1)"
        )
    )
