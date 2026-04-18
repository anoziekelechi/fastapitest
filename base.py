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
                          
   
