from ast import alias
from email.policy import default
from pydantic import SecretStr,field_validator,Field
from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from typing import Union,Literal
from pathlib import Path
#from sqlmodel import Field
from enum import StrEnum
from functools import lru_cache


class AppMode(StrEnum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TESTING = "testing"

class BaseAppSettings(BaseSettings):
    # APP SETTINGS
    app_mode:AppMode = Field(default=AppMode.DEVELOPMENT, alias="APP_MODE")
    #app_name: str = Field(default="E-COMMERCE API", alias="APP_NAME")
    #debug: bool = Field(default=False,alias="DEBUG") # production mode
     # DTABASE SETTINGS
    postgres_user:str =Field(..., alias=" POSTGRES_USER")
    postgres_password:SecretStr =Field(..., alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(..., alias="POSTGRES_HOST")
    postgres_db:str = Field(..., alias=" POSTGRES_DB")
    postgres_port:int = Field(..., alias="POSTGRES_PORT")
    # ____ EMAIL SETTINGS ____
    mail_username:str= Field(..., alias="MAIL_USERNAME")
    mail_password:SecretStr = Field(...,alias="MAIL_PASSWORD")
    mail_server:str = Field(..., alias="MAIL_SERVER")
    mail_from:str = Field(..., alias="MAIL_FROM")
    mail_port:int = Field(..., alias="MAIL_PORT")
    # CORS SETTING
    #cors_origin: list[str] = Field(default=["http://localhost:3000"],alias="CORS_ORIGIN")
    #___REDIS ___
    redis_url : str = Field(..., alias="REDIS_URL")
    image_dev_prefix: str = Field(default="development/",alias="IMAGE_DEV_PREFIX")
    image_prod_prefix: str = Field(default="production/", alias="IMAGE_PROD_PREFIX")
    
    # VALIDATORS CORS
    # @field_validator("cors_origin", mode="before")
    # @classmethod
    # def parse_cors_origins(cls, v):
    #     if isinstance(v, str):
    #         return [origin.strip() for origin in v.split(',')]
    #     return v
    
    def is_production(self) -> bool:
        return self.app_mode == AppMode.PRODUCTION
    @property
    def database_url(self) -> str:
        return(
            f"postgresql+asyncpg://"
            f"{self.postgres_user}:"
            f"{self.postgres_password.get_secret_value()}@"
            f"{self.postgres_host}:"
            f"{self.postgres_port}/"
            f"{self.postgres_db}"
        )
        #return f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password.get_secret_value()}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        
    @property
    def image_prefix(self) -> str:
         return self.image_prod_prefix if self.is_production() else self.image_dev_prefix
 # DEV SETTING
class DevSettings(BaseAppSettings):
    model_config = SettingsConfigDict(
    env_file = str(Path(__file__).parent.parent.parent.parent / ".env.development"),
    env_file_encoding='utf-8',
    extra="ignore",
    case_sensitive=False,
    )
    
class ProdSetting(BaseAppSettings):
    aws_region: str = Field(..., alias="AWS_REGION")
   
    model_config = SettingsConfigDict(
    env_file = None, #None, #lets docker injects
    env_file_encoding='utf-8',
    extra="ignore",
    case_sensitive=False,
    )
    
    
@lru_cache()
def get_settings() -> BaseAppSettings:
    env=os.getenv("APP_MODE", "develpment").lower()
    if env == "development":
        return DevSettings()
    else:
        return ProdSetting()
