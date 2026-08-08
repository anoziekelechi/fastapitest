#error
 INFO:     Application startup complete.
backend-1  | INFO:     192.168.65.1:51727 - "GET /admin/users HTTP/1.1" 500 Internal Server Error
backend-1  | ERROR:    Exception in ASGI application
backend-1  | Traceback (most recent call last):
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/protocols/http/httptools_impl.py", line 409, in run_asgi
backend-1  |     result = await app(  # type: ignore[func-returns-value]
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/middleware/proxy_headers.py", line 60, in __call__
backend-1  |     return await self.app(scope, receive, send)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/applications.py", line 1135, in __call__
backend-1  |     await super().__call__(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/applications.py", line 107, in __call__
backend-1  |     await self.middleware_stack(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/errors.py", line 186, in __call__
backend-1  |     raise exc
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/errors.py", line 164, in __call__
backend-1  |     await self.app(scope, receive, _send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/cors.py", line 85, in __call__
backend-1  |     await self.app(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/exceptions.py", line 63, in __call__
backend-1  |     await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
backend-1  |     raise exc
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
backend-1  |     await app(scope, receive, sender)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/middleware/asyncexitstack.py", line 18, in __call__
backend-1  |     await self.app(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 716, in __call__
backend-1  |     await self.middleware_stack(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 736, in app
backend-1  |     await route.handle(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 290, in handle
backend-1  |     await self.app(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 115, in app
backend-1  |     await wrap_app_handling_exceptions(app, request)(scope, receive, send)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
backend-1  |     raise exc
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
backend-1  |     await app(scope, receive, sender)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 101, in app
backend-1  |     response = await f(request)
backend-1  |                ^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 377, in app
backend-1  |     content = await serialize_response(
backend-1  |               ^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 215, in serialize_response
backend-1  |     raise ResponseValidationError(
backend-1  | fastapi.exceptions.ResponseValidationError: 1 validation error:
backend-1  |   {'type': 'list_type', 'loc': ('response',), 'msg': 'Input should be a valid list', 'input': {'total': 1, 'users': [ReadUser(id=2, surname='ANOZIE', othernames='KELECHI', email='kennedykelechijoseph@gmail.com', country=None, is_admin=True, verified=True, disabled=False, date_verified=None, created_at=datetime.datetime(2026, 8, 7, 11, 52, 0, 634136, tzinfo=datetime.timezone.utc))]}}
backend-1  | 
backend-1  |   File "/app/api/admin/routes.py", line 41, in list_users
backend-1  |     GET /admin/users
