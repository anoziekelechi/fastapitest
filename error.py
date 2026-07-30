File "/app/api/main.py", line 10, in <module>
backend-1  |     from api.users.routes import router as user_router
backend-1  |   File "/app/api/users/routes.py", line 94, in <module>
backend-1  |     @router.post(
backend-1  |      ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1063, in decorator
backend-1  |     self.add_api_route(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 1002, in add_api_route
backend-1  |     route = route_class(
backend-1  |             ^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 621, in __init__
backend-1  |     self.dependant = get_dependant(
backend-1  |                      ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 298, in get_dependant
backend-1  |     sub_dependant = get_dependant(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 276, in get_dependant
backend-1  |     param_details = analyze_param(
backend-1  |                     ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/dependencies/utils.py", line 501, in analyze_param
backend-1  |     field = create_model_field(
backend-1  |             ^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/utils.py", line 95, in create_model_field
backend-1  |     raise fastapi.exceptions.FastAPIError(
backend-1  | fastapi.exceptions.FastAPIError: Invalid args for response field! Hint: check that <class 'sqlalchemy.ext.asyncio.session.AsyncSession'> is a valid Pydantic field type. If you are using a return type annotation that is not a valid Pydantic field (e.g. Union[Response, dict, None]) you can disable generating the response model from the type annotation with the path operation decorator parameter response_model=None. Read more: https://fastapi.tiangolo.com/tutorial/response-model/

