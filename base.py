from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from fastapi import HTTPException, status, UploadFile, Depends
from sqlmodel import Session, select
from api.home.schemas import HomeResponse
from api.home.models import Home
from api.dependency import get_db
from api.core.file_storage import handle_file_update, get_public_url,validate_image_file_securely

# db: AsyncSession = Depends(get_db),

LOGO_MAX_SIZE = 5 * 1024 * 1024   # 5 MiB
HERO_MAX_SIZE = 8 * 1024 * 1024   # 8 MiB

class HomeImage:
    LOGO = "logo"
    BANNER ="banner"

async def setup_home_logic(
    sitename: str,
    db: AsyncSession = Depends(get_db),
    aboutus: Optional[str] = None,
    intro: Optional[str] = None,
    logo_key: Optional[UploadFile] = None,
    banner_key: Optional[UploadFile] = None,
   
) -> HomeResponse:
    """
    Create or update MAIN home configuration
    Returns response with public image URLs
    """
    stmt = select(Home).where(Home.config_type == "MAIN")
    current = (await db.execute(stmt)).scalars().first()
    #current = db.exec(stmt).first()

    # Handle file updates (with env-specific prefix)
    new_logo_key = await handle_file_update(
        file=logo_key,
        current_key=current.logo_key if current else None,
        prefix= HomeImage.LOGO,
        max_size=LOGO_MAX_SIZE,
        validator=validate_image_file_securely,
    )

    new_banner_key = await handle_file_update(
        file=banner_key,
        current_key=current.banner_key if current else None,
        prefix= HomeImage.BANNER,
        max_size=HERO_MAX_SIZE,
        validator=validate_image_file_securely,
    )

    try:
        if current is None:
            # First time creation
            record = Home(
                config_type="MAIN",
                sitename=sitename,
                aboutus=aboutus,
                intro=intro,
                logo_key=new_logo_key,
                banner_key=new_banner_key,
            )
            db.add(record)
        else:
            # Partial update
            current.sitename = sitename

            if aboutus is not None:
                current.aboutus = aboutus
            if intro is not None:
                current.intro = intro

            # Only update file keys if new file was provided
            if logo_key is not None:
                current.logo_key = new_logo_key
            if banner_key is not None:
                current.banner_key = new_banner_key

            record = current

        db.commit()
        db.refresh(record)

        return HomeResponse(
            sitename=record.sitename, # other homeresponse
            logo_url=get_public_url(record.logo_key),
            image_url=get_public_url(record.banner_key),
        )

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save home configuration: {str(e)}"
        ) from e


def get_home_settings_logic(db: AsyncSession = Depends(get_db)) -> HomeResponse:
    """
    Fetch the MAIN home configuration with public URLs
    """
    stmt = select(Home).where(Home.config_type == "MAIN")
    home = db.exec(stmt).first()

    if not home:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Home page settings not yet configured"
        )

    return HomeResponse(
        sitename=home.sitename,
        logo_url=get_public_url(home.logo_key),
        image_url=get_public_url(home.banner_key),
    )









#####

#my model

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
    
###
import uuid
from typing import Annotated
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlmodel import Session, select

router = APIRouter()

@router.post("/home/setup", status_code=201)
async def setup_home(
    sitename: Annotated[str, Form()],
    logo: Annotated[UploadFile, File()],
    image: Annotated[UploadFile, File()],
    session: Session = Depends(get_session)
):
    # 1. Secure Validation
    await validate_file_securely(logo)
    await validate_file_securely(image)

    # 2. Check if the "MAIN" singleton record already exists
    statement = select(Home).where(Home.config_type == "MAIN")
    home = session.exec(statement).first()
    
    # 3. Prepare unique S3 Keys
    logo_key = f"home/logo-{uuid.uuid4()}"
    image_key = f"home/hero-{uuid.uuid4()}"

    try:
        # 4. Stream uploads to S3 (Efficient for 2025)
        s3_client.upload_fileobj(logo.file, BUCKET_NAME, logo_key)
        s3_client.upload_fileobj(image.file, BUCKET_NAME, image_key)

        if not home:
            # CREATE: New record with the unique config_type
            home = Home(
                sitename=sitename, 
                config_type="MAIN", # Enforces the singleton via DB constraint
                logo_key=logo_key, 
                image_key=image_key
            )
            session.add(home)
        else:
            #delete old images
            if home.logo_key:
                s3_client.delete_object(Bucket=BUCKET_NAME, Key=home.logo_key)
            if home.image_key:
                s3_client.delete_object(Bucket=BUCKET_NAME, Key=home.image_key)
            
            # UPDATE: Replace fields on the existing "MAIN" record
            home.sitename = sitename
            home.logo_key = logo_key
            home.image_key = image_key
        
        session.commit()
        session.refresh(home)
        return {"message": "Home settings updated", "config_type": home.config_type}

    except Exception as e:
        session.rollback()
        # Optionally delete uploaded S3 files here if the DB commit fails
        raise HTTPException(status_code=500, detail="Failed to save settings")
    
    
@app.get("/home", response_model=HomeResponse)
def get_home_settings(session: Session = Depends(get_session)):
    # Always fetch the one marked "MAIN"
    statement = select(Home).where(Home.config_type == "MAIN")
    home = session.exec(statement).first()
    
    if not home:
        raise HTTPException(status_code=404, detail="Settings not initialized")

    return HomeResponse(
        sitename=home.sitename,
        logo_url=get_s3_url(home.logo_key), # Helper function for presigned URL
        image_url=get_s3_url(home.image_key)
    )
