# api/subscriptions/schemas.py
"""Subscription schemas."""
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# SERVICE
# =============================================================================

# api/subscriptions/models.py
"""Subscription models."""
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Column, String, Integer, ForeignKey,
    Boolean, Text, Numeric, UniqueConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel, utc_now

if TYPE_CHECKING:
    from api.firms.models import Firm


# api/firms/models.py
"""Firm and Branch models."""
from typing import TYPE_CHECKING, Optional
from sqlalchemy import (
    Column, String, Integer, ForeignKey, Text,
    UniqueConstraint
)
from sqlmodel import Field, Relationship
from api.core.base import BaseModel

if TYPE_CHECKING:
    from api.users.models import User
    from api.subscriptions.models import Subscription
    from api.receipts.models import Receipt



    receipts: list["Receipt"] = Relationship(back_populates="branch")
