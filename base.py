"""Base model for all database tables."""
from datetime import datetime, timezone

from sqlmodel import SQLModel, Field
from sqlalchemy import Column, DateTime, func


def utc_now() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


def timezone_column(onupdate=None):
    """
    Returns a fresh Column object for each model to prevent 
    'Column already assigned to table' error.
    """
    return lambda: Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        onupdate=onupdate,
    )


class BaseModel(SQLModel):
    """Abstract base model with timezone-aware timestamps."""
    
    __abstract__ = True
    
    id: int | None = Field(default=None, primary_key=True)
    
    # Created at
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=timezone_column(),                    # No onupdate
    )
    
    # Updated at
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=timezone_column(onupdate=func.now()), # With onupdate
    )


#old
from sqlmodel import Field,SQLModel
from typing import List,Optional
from sqlalchemy import func

from datetime import datetime,timezone 

def utc_now():
    return datetime.now(timezone.utc)

class BaseModel(SQLModel, table=True):
    __abstract__ = True
    id: Optional[int]= Field(default=None,primary_key=True)
    date_added : datetime =Field(
        default_factory= utc_now,
        sa_column_kwargs={"server_default":func.now()})
    date_modify : datetime =Field(
        default_factory=utc_now,
        sa_column_kwargs={"onupdate": func.now(),"server_default":func.now()})
                          
   
