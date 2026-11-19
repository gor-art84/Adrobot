from functools import lru_cache
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine


@lru_cache
def get_engine(database_path: Path) -> Engine:
    return create_engine(
        URL.create("sqlite+pysqlite", database=str(database_path.resolve()))
    )
