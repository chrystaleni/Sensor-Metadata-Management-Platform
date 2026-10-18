"""Initial sensor metadata schema."""
from alembic import op
from main import Base
revision='0001_initial'
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    bind=op.get_bind()
    if bind.dialect.name=='postgresql': bind.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS postgis')
    additive=('data_collection_configurations','activity_logs','operational_vocabulary')
    tables=[table for name,table in Base.metadata.tables.items() if name not in additive]
    Base.metadata.create_all(bind=bind,tables=tables)
def downgrade():
    Base.metadata.drop_all(bind=op.get_bind())
