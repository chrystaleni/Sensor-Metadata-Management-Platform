"""Ensure optional collection configuration and activity tables exist."""
from alembic import op
from main import Base

revision='0002_configuration_and_activity'
down_revision='0001_initial'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    Base.metadata.create_all(bind=bind,tables=[Base.metadata.tables['data_collection_configurations'],Base.metadata.tables['activity_logs']])

def downgrade():
    bind=op.get_bind()
    for table_name in ('activity_logs','data_collection_configurations'):
        Base.metadata.tables[table_name].drop(bind=bind,checkfirst=True)
