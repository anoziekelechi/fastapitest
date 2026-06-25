  
async def csrf_protection(request: Request):
    header = request.header.get("X_CSRF-TOKEN")
    cookie = request.cookie.get("csrf_token")
    
    if not header or not cookie or header != cookie:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,detail="Invalid CSRF token")




@router.get("/profile", response_model= ReadUser)
async def get_profile(user:User = Depends(current_user), _: None=Depends(csrf_protection)):
    return ReadUser.model_validate(user)
    
