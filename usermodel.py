from email.policy import default
from datetime import datetime, timezone
from sqlalchemy import Column, Text, Boolean, DateTime,String,Integer, ForeignKey
from api.core.base import BaseModel
from sqlmodel import Field,Relationship,UniqueConstraint
from pydantic import  EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import List
from api.home.models import Country
from hashlib import md5



class Group(BaseModel,table=True):
    __tablename__ = "groups"
    name: str = Field(
        sa_column=Column(String(30),unique=True,nullable=False,index=True)
        )
    permission: str = Field(
        sa_column=Column(String(30),unique=True,nullable=False,index=True)
    )
    
    users: List["User"] = Relationship(back_populates="group", sa_relationship_kwargs={"passive_deletes":True})
    

class User(BaseModel, table=True):
    __tablename__ = "users"
    
    group_id: int | None = Field(
        default = None,
        sa_column=Column(
            Integer,
            ForeignKey("groups.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )
    )
    
    country_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("countries.id", ondelete="CASCADE"),
            nullable= False
        )
    )
    surname: str = Field(
        sa_column=Column(String(20),nullable=False)
    )
    othernames: str = Field(
        sa_column=Column(String(50),nullable=False)
    )
    email: EmailStr=Field(
        sa_column=Column(String(50),nullable=False,unique=True,index=True)
    )
    hashed_password: str = Field(
        sa_column=Column(String(128),nullable=False)
    )
    is_admin: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,nullable=False,server_default="false"
        )
    )
    disabled: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,nullable=False,server_default="false"
        )
    )
    payment_id: str | None = Field(sa_column=Column(String(128),nullable=True))
    one_click: bool =Field(
        default=False,
        sa_column=Column(
            Boolean,nullable=True,server_default="false"
        )
    )
    verified: bool = Field(
        default=False,
        sa_column=Column(
            Boolean,nullable=False,server_default="false"
        )
    )
    date_verified: datetime | None = Field(
        default= None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True
        )
    )
  
    #team: Team | None = Relationship(back_populates="user",sa_relationship_kwargs={"userlist":False})
    
    # Relationship to access country data directly
    country: "Country | None" = Relationship(back_populates="user")
    
    
    group: Group | None = Relationship(back_populates="users")
    team: "Team | None" = Relationship(back_populates="users")
    
    # added_product: List["Product"] = Relationship(back_populates="added_by")
    # inventory_entries: List["Inventory"] = Relationship(back_populates="added_by")
    
    def avatar(self,size: int = 128) ->str:
        digest=md5(self.email.lower().encode('utf-8')).hexdigest()
        return f"https://www.gravatar.com/avatar/{digest}?d=identicon&s=(size)"
    
    
    
   
class Team(BaseModel,table=True):
    __tablename__ = "teams"
    user_id: int = Field (
        default= None,
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable= True,
            index=True
        )
    )
    position:str = Field(
        sa_column=Column(String(50),nullable=False)
    )
    education:str = Field(
        sa_column=Column(String(200),nullable=False)
    )
    
    user: User = Relationship(back_populates="team")

    
