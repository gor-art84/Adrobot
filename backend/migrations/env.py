from alembic import context

from app.config import Settings
from app.db.models import Base
from app.db.session import get_engine


engine = get_engine(Settings().database_path)

if context.is_offline_mode():
    context.configure(
        dialect_name=engine.dialect.name,
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
