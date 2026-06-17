
async def create_group(*, db: AsyncSession, data:GroupCreate) -> dict:
    if (await db.exec(select(Group).where(Group.name == data.name))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{data.name} Group Alredy exist")
    
    if (await db.exec(select(Group).where(Group.permission == data.permission))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{data.permission} permission  Alredy assigned")
    
    group_perm = Group(
        name = data.name,
        permission = data.permission
    )
    
    db.add(group_perm)
    await db.commit()
    await db.refresh(group_perm)
    
    return{"message":"Group with Permission added successfully"}
    
     
     
     
async def list_group(*, db:AsyncSession, skip: int=0, limit: int=100) -> List[Group]:  
    result = await db.exec(select(Group).order_by(Group.name).offset(skip).limit(limit))
    return result.scalars().all() 


async def update_group(group_id: int, db: AsyncSession,data:GroupUpdate) -> Group: # dict
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Group not found")
    
    #manual update 100% safe than automatic
    if data.name is not None and data.name != group.name:
        exist = (await db.exec(select(Group).where(Group.name == data.nane))).first()
        if exist:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exist")
        group.name = data.name
        
        
    if data.permission is not None and data.permission != group.permission:
        exist = (await db.exec(select(Group).where(Group.permission == data.permission))).first()
        if exist:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Permission name already exist")
        group.permission = data.permission
        
    await db.commit()
    await db.refresh(group)
    return group
    #in future return dictionary with csrf token
    
    
async def delete_group(group_id:int, db:AsyncSession):
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail="Group not found")
    if group.users:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Cannot delete group with assigned users")
    await db.delete(group)
    await db.commit()
    return{"message":f"Group '{group.name}' deleted"}

async def assign_to_group(data: UserGroup,db:AsyncSession):
    user = (await db.exec(select(User).where(User.email== user.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    group = (await db.exec(select(Group).where(Group.name == data.group_name))).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"Group with name {data.group_name} not found")
    if user.group_id == group.id:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User already on this group")
    
    user.group_id = group.id
    await db.commit()
    await db.refresh(user)
    return {"message":f"User with {data.email} assigned to {data.group_name}"}

async def remove_user_from_group(data: UsersAction,db: AsyncSession):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if user.group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail="Use not in any group")

    group_name = await db.get(Group, user.group_id).name
    user.group_id = None
    await db.commit()
    await db.refresh(user)
    return {"msg":f"User {data.email} successfully removed from group {group_name}"}

async def disable_user(db:AsyncSession,data:UsersAction):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if user.disabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User with email {data.email} already disabled")
    user.disabled = True
    await db.commit()
    await db.refresh(user)
    return {"message":f"user {data.email} has been disabled"}

async def enable_user(db:AsyncSession,data:UsersAction):
    user = (await db.exec(select(User).where(User.email== data.email))).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,detail=f"User with email {data.email} not found")
    
    if not user.disabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,detail=f"User with email {data.email} is active")
    user.disabled = False
    await db.commit()
    await db.refresh(user)
    return {"message":f"user {data.email} has been re-activated"}
    
    
    
    ###schemas

FORBIDEN_WORDS = {"admin","root","is_admin"}
def normalize_words(value: str) -> str:
    if not value:
        raise ValueError("Field cannot be empty")
    field = value.strip()
    if not field:
        raise ValueError("Field cannot be empty after strip")
     #Accept only safe characters
    if not re.fullmatch(r'^[a-zA-Z_]+',value):
        raise ValueError("only letters and underscore allowed")
    
    #block forbiden words
    lowerd = value.lower()
    for words in FORBIDEN_WORDS:
        if words in lowerd:
            raise ValueError("Invalid names detected")
        
# convert to snake case
    normalized = re.sub(r"[_\s]+", "_",lowerd).strip("_")
    if not normalized:
        raise ValueError("result not found")
    return normalized
    

class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    permission: str
    
    @field_validator("name","permission",mode="before")
    @classmethod
    def validate_name_permission(cls, value: str) -> str:
        return normalize_words(value)


class GroupRead(BaseModel):  
    id:int
    name: str
    permission: str
    date_added: datetime 
    date_modify:datetime   
        # only needed in read
    class Config:
        from_attributes=True
        
    # manufacturers:List[ManufacturerRead]=[]
    # products:List[ProductRead]=[]
    
class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str  | None = None
    permission: str | None = None
    
    @field_validator("name","permission",mode="before")
    @classmethod
    def validate_name_permission(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_words(value)
    
    
class UsersGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email:str
    group_name: str
    
    
    
class UsersAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    
    



    
   ###routes

from src.ecommerce.users.model import Group, User
from src.ecommerce.admin.schemas import GroupCreate,GroupRead,UsersGroup, UsersAction,GroupUpdate
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, APIRouter
from src.ecommerce.admin.logics import create_group,remove_user_from_group,disable_user,enable_user,assign_to_group,list_group, \
    update_group,delete_group
from src.ecommerce.users.logics import require_admin
from src.ecommerce.dependency import DB

router = APIRouter(prefix="/admin",tags=["admin"])


@router.post("/add_group",response_model=GroupRead)
async def create_group_perm(
     db: DB,
    data: GroupCreate,
    user: User = Depends(require_admin()),
    
    ):
    return await create_group(data=data,db=db)

@router.get("/list_groups")
async def all_groups(db:DB,user:User = Depends(require_admin()),):
    return await list_group(db=db)

@router.put("/group/{group_id}")
async def update_groups(db:DB,group_id:int,data:GroupUpdate, user: User = Depends(require_admin())):
    return await update_group(group_id=group_id,db=db,data=data)


@router.delete("/delete_group/{group_id}")
async def delete_groups(group_id: int, db:DB,user: User = Depends(require_admin()),):
    return await delete_group(group_id=group_id, db=db)




@router.post("/add_to_group",response_model=GroupRead)
async def create_group_perm(
    data: UsersGroup,
    db:DB,
    user: User = Depends(require_admin()),
  
    ):
    return await assign_to_group(data=data,db=db)

@router.post("/remove_from_group",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
   
    ):
    return await remove_user_from_group(data=data,db=db)


@router.post("/diable_user",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
 
    ):
    return await disable_user(data=data,db=db)


@router.post("/enable_user",response_model=GroupRead)
async def create_group_perm(
    data: UsersAction,
    db:DB,
    user: User = Depends(require_admin()),
  
    ):
    return await enable_user(data=data,db=db)









        
    
    
    
    


    
    
