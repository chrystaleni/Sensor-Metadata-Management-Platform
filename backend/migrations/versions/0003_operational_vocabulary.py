"""Add separate measurement/value operational vocabulary pairs."""
from alembic import op
from main import Base

revision='0003_operational_vocabulary'
down_revision='0002_configuration_and_activity'
branch_labels=None
depends_on=None

def upgrade():
    Base.metadata.tables['operational_vocabulary'].create(bind=op.get_bind(),checkfirst=True)

def downgrade():
    Base.metadata.tables['operational_vocabulary'].drop(bind=op.get_bind(),checkfirst=True)
