"""Run an Alembic round trip against an explicitly named disposable database."""

from __future__ import annotations

import os
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: migration_roundtrip.py <database-name>")

    database = sys.argv[1].strip()
    if database != "hireflow_migration_check":
        raise SystemExit(f"refusing unexpected migration database: {database!r}")

    base_url = os.environ["HIREFLOW_MIGRATION_DATABASE_URL"]
    migration_url = make_url(base_url).set(database=database)
    rendered_url = migration_url.render_as_string(hide_password=False)
    os.environ["HIREFLOW_MIGRATION_DATABASE_URL"] = rendered_url

    engine = create_engine(rendered_url)
    try:
        with engine.connect() as connection:
            actual = connection.execute(text("select current_database()"))
            if actual.scalar_one() != database:
                raise RuntimeError("migration connection did not select the disposable database")
    finally:
        engine.dispose()

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
