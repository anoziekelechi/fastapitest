from sqlalchemy import table
from api.core.base import BaseModel
from sqlmodel import Field,Relationship,UniqueConstraint
from pydantic import PositiveInt, EmailStr
from pydantic_extra_types.phone_numbers import PhoneNumber
from typing import List
from hashlib import md5

class Home(BaseModel, table=True):
    config_type:str = Field(default="MAIN",unique=True,index=True,nullable=False)
    sitename: str = Field(...,max_length=50)
    intro:  str  = Field(..., sa_type="TEXT")
    aboutus:str  = Field(..., sa_type="TEXT")
    mission:str  = Field(..., sa_type="TEXT")
    vission:str   = Field(..., sa_type="TEXT")
    logo_key: str | None = None
    banner_key: str | None = None
    
    
  
class Country(BaseModel,table=True):
    name:str = Field(..., unique=True, index=True, max_length=30)
    currency_code:str = Field(..., min_length=3, max_length=3)
    whatsapp: PositiveInt 
     # LINK BACK TO offices
    office: List["Offices"] = Relationship(back_populates="country")
    
    
    
    
class Offices(BaseModel,table=True):
    # FK LINKING TO COUNTRY TABLE
    country_id: int = Field (foreign_key="country.id", sa_column_kwargs={"ondelete":"CASCADE"})
    address:str = Field(..., sa_type="TEXT")
    whatsapp:PositiveInt | None = None
    phone_number:PhoneNumber | None = None #install "pydantic-extra-types[phonenumbers]"
    email: EmailStr | None = Field(default=None,index=True,unique=True)
    # Relationship to access country data directly
    country: Country| None = Relationship(back_populates="country")
    
    
    
class Team(BaseModel,table=True):
    surname:str = Field(...,max_length=50)
    othernames:str = Field(...,max_length=50)
    position:str = Field(...,max_length=50)
    email: EmailStr = Field(..., index=True,unique=True)
    
    @property
    def avatar(self,size=128):
        digest=md5(self.email.lower().encode('utf-8')).hexdigest()
        return f"https://www.gravatar.com/avatar/{digest}?d=identicon&s=(size)"
