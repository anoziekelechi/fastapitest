#api/uswes/schema.py 
 # ✅ Set as cookies only - never in body
    set_auth_cookies(
        response=response,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        csrf_token=tokens.csrf_token,
    )



Cannot access attribute "access_token" for class "TokenResponse"
  Attribute "access_token" is unknownPylancereportAttributeAccessIssue
(function) access_token: Unknown



Variable not allowed in type expressionPylancereportInvalidTypeForm
(variable) RedisDep: Unknown


    
