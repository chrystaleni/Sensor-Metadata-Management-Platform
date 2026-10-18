import os, sys
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from main import Base
config=context.config
if config.config_file_name: fileConfig(config.config_file_name)
config.set_main_option('sqlalchemy.url',os.getenv('DATABASE_URL',config.get_main_option('sqlalchemy.url')))
target_metadata=Base.metadata
def run_migrations_offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'),target_metadata=target_metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if connection.dialect.name=='postgresql': connection.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS postgis')
        context.configure(connection=connection,target_metadata=target_metadata)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
