# print_tables.py

from database import Base  # or wherever your models and Base are defined
from models import Notification, PunishmentAction, PunishmentReason, ContributionStatus, GroupStatus, MemberStatus, NotificationType, UserOAuthToken

print("Registered tables:")
print(Base.metadata.tables.keys())
