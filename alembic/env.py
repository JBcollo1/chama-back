import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Add the root path so Python can import your modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import Base and models
from models import Base
from models import MemberPunishment, Group, Profile, GroupMember, GroupAdmin, Contribution
from models import Notification, PunishmentAction, PunishmentReason, ContributionStatus, GroupStatus, MemberStatus, NotificationType

from database import DATABASE_URL



# Alembic Config
config = context.config

# Logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Pass metadata for Alembic autogeneration
target_metadata = Base.metadata
