from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from fastapi import HTTPException, status, UploadFile, Depends
from sqlmodel import Session, select
from api.home.schemas import ReadHome
from api.models import Home
from api.core.database import get_db
from api.core.file_storage import handle_file_update, get_public_url,validate_image_file_securely

# db: AsyncSession = Depends(get_db),

LOGO_MAX_SIZE = 5 * 1024 * 1024   # 5 MiB
HERO_MAX_SIZE = 8 * 1024 * 1024   # 8 MiB

class HomeImage:
    LOGO = "logo"
    BANNER ="banner"

async def setup_home_logic(
    #db: AsyncSession = Depends(get_db),
    sitename: str,
    db: AsyncSession = Depends(get_db),
    aboutus: Optional[str] = None,
    intro: Optional[str] = None,
    mission: Optional[str] = None,
    vision: Optional[str] = None,
    logo_key: Optional[UploadFile] = None,
    banner_key: Optional[UploadFile] = None,
   
) -> dict[str, str]:
    """
    Create or update MAIN home configuration
    Returns response with public image URLs
    """
    stmt = select(Home).where(Home.config_type == "MAIN")
    current = (await db.execute(stmt)).scalars().first()

    # Handle file updates (with env-specific prefix)
    new_logo_key = await handle_file_update(
        file=logo_key,
        current_key=current.logo_key if current else None,
        prefix= HomeImage.LOGO,# use LOGO_FOLDER
        max_size=LOGO_MAX_SIZE,
        validator=validate_image_file_securely,
    )

    new_banner_key = await handle_file_update(
        file=banner_key,
        current_key=current.banner_key if current else None,
        prefix= HomeImage.BANNER, #BANNER_FOLDER
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
                mission=mission,
                vision=vision,
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
            if mission is not None:
                current.mission = mission
            if vision is not None:
                current.vission = vision

            # Only update file keys if new file was provided
            if logo_key is not None:
                current.logo_key = new_logo_key
            if banner_key is not None:
                current.banner_key = new_banner_key

            record = current

        db.commit()
        db.refresh(record)

        return {"message":"Home settings updated successfully", "status":"success"}

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save home configuration: {str(e)}"
        ) from e


async def get_home_settings_logic(db: AsyncSession = Depends(get_db)) -> ReadHome:
    """
    Fetch the MAIN home configuration with public URLs
    """
    stmt = select(Home).where(Home.config_type == "MAIN")
    home = (await db.execute(stmt)).scalars().first()
   

    if not home:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Home page settings not yet configured"
        )

    return ReadHome(
        sitename=home.sitename, 
        intro=home.intro,
        aboutus=home.aboutus,
        mission=home.mission,
        vision=home.vision,
        logo_key=get_public_url(home.logo_key),
        banner_key=get_public_url(home.banner_key),
    )  # type: ignore



# routes


from typing import Annotated
from fastapi import APIRouter, Form, Depends, status, UploadFile, File
from api.home.schemas import ReadHome
from api.core.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from api.home.logics import setup_home_logic, get_home_settings_logic



router = APIRouter(prefix="/home", tags=["Home Configuration"])


@router.get("/")
def  health():
   
    return {
        "message":"welcome to ecomarket home of high  quality",

       }

@router.post( "/setup",status_code=status.HTTP_201_CREATED,response_model=ReadHome,)
async def setup_home(
    sitename: Annotated[str, Form(min_length=1, max_length=120)],
    aboutus: Annotated[str | None, Form(max_length=2000)] = None,
    intro: Annotated[str | None, Form(max_length=1200)] = None,
    mission: Annotated[str | None, Form(max_length=1200)] = None,
    vision: Annotated[str | None, Form(max_length=1200)] = None,
    
    logo_key: Annotated[UploadFile | None, File()] = None,
    banner_key: Annotated[UploadFile | None, File()] = None,
    
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """
    Endpoint for admin to configure home page settings
    """
    return await setup_home_logic(
        db=db,
        sitename=sitename,
        aboutus=aboutus,
        intro=intro,
        mission=mission,
        vision=vision,
        logo_key=logo_key,
        banner_key=banner_key,
    )


@router.get("/home1",response_model=ReadHome)
async def get_home_settings(
    db: AsyncSession = Depends(get_db)
) -> ReadHome:
    """
    Public endpoint to fetch home page configuration
    """
    return await get_home_settings_logic(db=db)







### schemas
from sqlmodel import SQLModel

class ReadHome(SQLModel):
    id: int
    sitename:str  
    intro:str | None = None
    aboutus:str | None = None
    mission:str | None = None
    vision:str | None = None
    logo_key:str | None = None
    banner_key: str | None = None
