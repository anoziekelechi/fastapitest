 File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 516, in _resolve_name
backend-1  |     rval = d[token]
backend-1  |            ~^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_collections.py", line 345, in __missing__
backend-1  |     self[key] = val = self.creator(key)
backend-1  |                       ^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 484, in _access_cls
backend-1  |     return self.fallback[key]
backend-1  |            ~~~~~~~~~~~~~^^^^^
backend-1  | KeyError: 'Country | None'
backend-1  | 
backend-1  | The above exception was the direct cause of the following exception:
backend-1  | 
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
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 355, in app
backend-1  |     raw_response = await run_endpoint_function(
backend-1  |                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 243, in run_endpoint_function
backend-1  |     return await dependant.call(**values)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/api/home/routes.py", line 56, in get_home_settings
backend-1  |     return await get_home_settings_logic(db=db)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/api/home/logics.py", line 108, in get_home_settings_logic
backend-1  |     home = (await db.execute(stmt)).scalars().first()
backend-1  |             ^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 449, in execute
backend-1  |     result = await greenlet_spawn(
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
backend-1  |     result = context.switch(value)
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2351, in execute
backend-1  |     return self._execute_internal(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2249, in _execute_internal
backend-1  |     result: Result[Any] = compile_state_cls.orm_execute_statement(
backend-1  |                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 306, in orm_execute_statement
backend-1  |     result = conn.execute(
backend-1  |              ^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
backend-1  |     return meth(
backend-1  |            ^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
backend-1  |     return connection._execute_clauseelement(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1633, in _execute_clauseelement
backend-1  |     compiled_sql, extracted_params, cache_hit = elem._compile_w_cache(
backend-1  |                                                 ^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 716, in _compile_w_cache
backend-1  |     compiled_sql = self._compiler(
backend-1  |                    ^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 324, in _compiler
backend-1  |     return dialect.statement_compiler(dialect, self, **kw)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 1447, in __init__
backend-1  |     Compiled.__init__(self, dialect, statement, **kwargs)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 887, in __init__
backend-1  |     self.string = self.process(self.statement, **compile_kwargs)
backend-1  |                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 933, in process
backend-1  |     return obj._compiler_dispatch(self, **kwargs)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/visitors.py", line 138, in _compiler_dispatch
backend-1  |     return meth(self, **kw)  # type: ignore  # noqa: E501
backend-1  |            ^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 4782, in visit_select
backend-1  |     compile_state = select_stmt._compile_state_factory(
backend-1  |                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/base.py", line 701, in create_for_statement
backend-1  |     return klass.create_for_statement(statement, compiler, **kw)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 447, in create_for_statement
backend-1  |     return cls._create_orm_context(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 1175, in _create_orm_context
backend-1  |     _QueryEntity.to_compile_state(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 2628, in to_compile_state
backend-1  |     _MapperEntity(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 2708, in __init__
backend-1  |     entity._post_inspect
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 1338, in __get__
backend-1  |     obj.__dict__[self.__name__] = result = self.fget(obj)
backend-1  |                                            ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2733, in _post_inspect
backend-1  |     self._check_configure()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2410, in _check_configure
backend-1  |     _configure_registries({self.registry}, cascade=True)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 4227, in _configure_registries
backend-1  |     _do_configure_registries(registries, cascade)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 4268, in _do_configure_registries
backend-1  |     mapper._post_configure_properties()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2427, in _post_configure_properties
backend-1  |     prop.init()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/interfaces.py", line 595, in init
backend-1  |     self.do_init()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1660, in do_init
backend-1  |     self._generate_backref()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 2144, in _generate_backref
backend-1  |     self._add_reverse_property(self.back_populates)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1612, in _add_reverse_property
backend-1  |     other._setup_entity()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1865, in _setup_entity
backend-1  |     self._clsregistry_resolve_name(argument)(),
backend-1  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 520, in _resolve_name
backend-1  |     self._raise_for_name(name, err)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 501, in _raise_for_name
backend-1  |     raise exc.InvalidRequestError(
backend-1  | sqlalchemy.exc.InvalidRequestError: When initializing mapper Mapper[Offices(offices)], expression 'Country | None' failed to locate a name ('Country | None'). If this is a class name, consider adding this relationship() to the <class 'api.models.home.Offices'> class after both dependent classes have been defined.
Gracefully stopping... (press Ctrl+C again to force)
[+] Stopping 1/1
 ✔ Container ecommerce-backend-1  Stopped                                                    0.9s 
anoziekelechi@Anozies-MacBook-Pro Ecommerce % docker compose restart backend
WARN[0000] The "POSTGRES_DB" variable is not set. Defaulting to a blank string. 
WARN[0000] The "POSTGRES_USER" variable is not set. Defaulting to a blank string. 
[+] Restarting 1/1
 ✔ Container ecommerce-backend-1  Started                                                    0.2s 
anoziekelechi@Anozies-MacBook-Pro Ecommerce % 
anoziekelechi@Anozies-MacBook-Pro Ecommerce % docker compose up backend     
WARN[0000] The "POSTGRES_DB" variable is not set. Defaulting to a blank string. 
WARN[0000] The "POSTGRES_USER" variable is not set. Defaulting to a blank string. 
[+] Running 3/3
 ✔ Container ecommerce-redis-1    Running                                                    0.0s 
 ✔ Container ecommerce-db-1       Running                                                    0.0s 
 ✔ Container ecommerce-backend-1  Running                                                    0.0s 
Attaching to backend-1
Gracefully stopping... (press Ctrl+C again to force)
[+] Stopping 1/1
 ✔ Container ecommerce-backend-1  Stopped                                                    0.7s 
anoziekelechi@Anozies-MacBook-Pro Ecommerce % docker compose up backend     
WARN[0000] The "POSTGRES_DB" variable is not set. Defaulting to a blank string. 
WARN[0000] The "POSTGRES_USER" variable is not set. Defaulting to a blank string. 
[+] Running 3/3
 ✔ Container ecommerce-redis-1    Running                                                    0.0s 
 ✔ Container ecommerce-db-1       Running                                                    0.0s 
 ✔ Container ecommerce-backend-1  Created                                                    0.0s 
Attaching to backend-1
backend-1  | INFO:     Will watch for changes in these directories: ['/app']
backend-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
backend-1  | INFO:     Started reloader process [1] using WatchFiles
backend-1  | INFO:     Started server process [8]
backend-1  | INFO:     Waiting for application startup.
backend-1  | INFO:api.main:starting application
backend-1  | INFO:api.main:database connection pool ready
backend-1  | INFO:api.main:redis connected successfully -><coroutine object Redis.execute_command at 0x7ffff8b7fab0>
backend-1  | INFO:api.main:Email service running
backend-1  | INFO:api.main:All services running
backend-1  | INFO:     Application startup complete.
backend-1  | INFO:     192.168.65.1:18768 - "GET /home/home1 HTTP/1.1" 500 Internal Server Error
backend-1  | ERROR:    Exception in ASGI application
backend-1  | Traceback (most recent call last):
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 516, in _resolve_name
backend-1  |     rval = d[token]
backend-1  |            ~^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_collections.py", line 345, in __missing__
backend-1  |     self[key] = val = self.creator(key)
backend-1  |                       ^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 484, in _access_cls
backend-1  |     return self.fallback[key]
backend-1  |            ~~~~~~~~~~~~~^^^^^
backend-1  | KeyError: 'Country | None'
backend-1  | 
backend-1  | The above exception was the direct cause of the following exception:
backend-1  | 
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
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 355, in app
backend-1  |     raw_response = await run_endpoint_function(
backend-1  |                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 243, in run_endpoint_function
backend-1  |     return await dependant.call(**values)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/api/home/routes.py", line 56, in get_home_settings
backend-1  |     return await get_home_settings_logic(db=db)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/api/home/logics.py", line 108, in get_home_settings_logic
backend-1  |     home = (await db.execute(stmt)).scalars().first()
backend-1  |             ^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 449, in execute
backend-1  |     result = await greenlet_spawn(
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
backend-1  |     result = context.switch(value)
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2351, in execute
backend-1  |     return self._execute_internal(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2249, in _execute_internal
backend-1  |     result: Result[Any] = compile_state_cls.orm_execute_statement(
backend-1  |                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 306, in orm_execute_statement
backend-1  |     result = conn.execute(
backend-1  |              ^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
backend-1  |     return meth(
backend-1  |            ^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
backend-1  |     return connection._execute_clauseelement(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1633, in _execute_clauseelement
backend-1  |     compiled_sql, extracted_params, cache_hit = elem._compile_w_cache(
backend-1  |                                                 ^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 716, in _compile_w_cache
backend-1  |     compiled_sql = self._compiler(
backend-1  |                    ^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 324, in _compiler
backend-1  |     return dialect.statement_compiler(dialect, self, **kw)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 1447, in __init__
backend-1  |     Compiled.__init__(self, dialect, statement, **kwargs)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 887, in __init__
backend-1  |     self.string = self.process(self.statement, **compile_kwargs)
backend-1  |                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 933, in process
backend-1  |     return obj._compiler_dispatch(self, **kwargs)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/visitors.py", line 138, in _compiler_dispatch
backend-1  |     return meth(self, **kw)  # type: ignore  # noqa: E501
backend-1  |            ^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/compiler.py", line 4782, in visit_select
backend-1  |     compile_state = select_stmt._compile_state_factory(
backend-1  |                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/base.py", line 701, in create_for_statement
backend-1  |     return klass.create_for_statement(statement, compiler, **kw)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 447, in create_for_statement
backend-1  |     return cls._create_orm_context(
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 1175, in _create_orm_context
backend-1  |     _QueryEntity.to_compile_state(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 2628, in to_compile_state
backend-1  |     _MapperEntity(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/context.py", line 2708, in __init__
backend-1  |     entity._post_inspect
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 1338, in __get__
backend-1  |     obj.__dict__[self.__name__] = result = self.fget(obj)
backend-1  |                                            ^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2733, in _post_inspect
backend-1  |     self._check_configure()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2410, in _check_configure
backend-1  |     _configure_registries({self.registry}, cascade=True)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 4227, in _configure_registries
backend-1  |     _do_configure_registries(registries, cascade)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 4268, in _do_configure_registries
backend-1  |     mapper._post_configure_properties()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2427, in _post_configure_properties
backend-1  |     prop.init()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/interfaces.py", line 595, in init
backend-1  |     self.do_init()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1660, in do_init
backend-1  |     self._generate_backref()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 2144, in _generate_backref
backend-1  |     self._add_reverse_property(self.back_populates)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1612, in _add_reverse_property
backend-1  |     other._setup_entity()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1865, in _setup_entity
backend-1  |     self._clsregistry_resolve_name(argument)(),
backend-1  |     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 520, in _resolve_name
backend-1  |     self._raise_for_name(name, err)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/clsregistry.py", line 501, in _raise_for_name
backend-1  |     raise exc.InvalidRequestError(
backend-1  | sqlalchemy.exc.InvalidRequestError: When initializing mapper Mapper[Offices(offices)], expression 'Country | None' failed to locate a name ('Country | None'). If this is a class name, consider adding this relationship() to the <class 'api.models.home.Offices'> class after both dependent classes have been defined.
