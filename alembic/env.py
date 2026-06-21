import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = None


def _resolve_admin_url() -> str:
    """Resolve the migration connection URL, environment-portable.

    Precedence:
      1. POSTGRES_ADMIN_URL from the process environment (set by `make`, which
         does `include .env; export`, and by cloud deploy environments).
      2. POSTGRES_ADMIN_URL loaded from the repo-root .env as a fallback, so a
         bare `uv run alembic ...` (not via make) still works locally.
      3. sqlalchemy.url from alembic.ini (intentionally blank by default).

    No host/port/role is hardcoded here. A missing URL is a hard, explicit
    failure rather than a silent fallback to a wrong target.
    """
    url = os.environ.get("POSTGRES_ADMIN_URL")

    if not url:
        # Fallback: load the repo-root .env (dependency available in the venv).
        # override=False so a real environment variable always wins.
        try:
            from dotenv import load_dotenv

            repo_root = Path(__file__).resolve().parents[1]
            load_dotenv(repo_root / ".env", override=False)
            url = os.environ.get("POSTGRES_ADMIN_URL")
        except Exception:  # pragma: no cover - dotenv is best-effort here
            url = None

    if not url:
        url = config.get_main_option("sqlalchemy.url") or None

    if not url:
        raise RuntimeError(
            "No migration database URL. Set POSTGRES_ADMIN_URL in the "
            "environment (local: it lives in .env and `make` exports it; "
            "cloud: export it before `alembic upgrade head`)."
        )

    return url


def _log_target(url: str) -> None:
    """Print the resolved target host/port/database (never the password) so it
    can be eyeballed before any DDL runs. Guards against pointing the migration
    at the wrong instance (e.g. Customer Master on 5432)."""
    parsed = make_url(url)
    sys.stderr.write(
        f"[alembic] migration target -> host={parsed.host} port={parsed.port} "
        f"database={parsed.database} user={parsed.username}\n"
    )
    sys.stderr.flush()


# Resolve once and make the URL authoritative for both offline and online modes.
_ADMIN_URL = _resolve_admin_url()
_log_target(_ADMIN_URL)
config.set_main_option("sqlalchemy.url", _ADMIN_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=_ADMIN_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
