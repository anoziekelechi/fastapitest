from __future__ import annotations
from typing import TYPE_CHECKING
from sqlalchemy import Column, Text, Integer, ForeignKey,String
from api.core.base import BaseModel
from sqlmodel import Field,Relationship,UniqueConstraint
from pydantic import  EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import List

if TYPE_CHECKING:
    from api.models.users import User


class Home(BaseModel, table=True):
    __tablename__ = "home" # type: ignore
    config_type:str = Field(
        sa_column=Column(String(50),nullable=False,unique=True,index=True),
        min_length=2,max_length=50
        )                        
    sitename: str = Field(
        sa_column=Column(String(50),nullable=False),min_length=7,max_length=50
    )
    intro:  str | None  = Field(default=None,sa_column=Column(Text,nullable=True))
    aboutus:str | None = Field(default=None,sa_column=Column(Text,nullable=True))
    mission:str | None  = Field(default=None,sa_column=Column(Text,nullable=True))
    vision:str | None  = Field(default=None,sa_column=Column(Text,nullable=True))
    logo_key: str | None = Field(default=None,sa_column=Column(String(255),nullable=True))
    
    banner_key: str | None = Field(default=None,sa_column=Column(String(255),nullable=True))
    
    
  
class Country(BaseModel,table=True):
    __tablename__ = "countries"  # type: ignore
    name:str = Field(
        sa_column=Column(String(30),nullable=False,unique=True, index=True)
    )
       
    currency_code:str = Field(
        sa_column=Column(String(3),nullable=False),min_length=3, max_length=3,
    )
   
    whatsapp:int| None = Field(default=None, gt=0)
     # LINK BACK TO offices
    office: list["Offices"] = Relationship(back_populates="country") # also use list
    
       # LINK BACK TO USERS
    users: list["User"] = Relationship(back_populates="country")
    
    
    
    
class Offices(BaseModel,table=True):
    __tablename__ = "offices"  # type: ignore
    # FK LINKING TO COUNTRY TABLE
    country_id: int = Field (
        sa_column=Column(
            Integer,
            ForeignKey("countries.id", ondelete="CASCADE"),
            nullable= False
        )
    )
    address:str | None = Field(default=None,sa_column=Column(Text, nullable=True))
    whatsapp:int| None = Field(default=None, gt=0)
    phone_number:str | None = Field(default=None,sa_column=Column(String(20),nullable=True))
    email: EmailStr | None = Field(default=None,sa_column=Column(String(50),index=True,unique=True))
    # Relationship to access country data directly
    country: Country| None = Relationship(back_populates="country")
