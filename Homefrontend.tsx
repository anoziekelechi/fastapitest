 Traceback (most recent call last):
backend-1   |   File "asyncpg/protocol/prepared_stmt.pyx", line 175, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
backend-1   |   File "asyncpg/protocol/codecs/base.pyx", line 251, in asyncpg.protocol.protocol.Codec.encode
backend-1   |   File "asyncpg/protocol/codecs/base.pyx", line 153, in asyncpg.protocol.protocol.Codec.encode_scalar
backend-1   |   File "asyncpg/pgproto/codecs/int.pyx", line 60, in asyncpg.pgproto.pgproto.int4_encode
backend-1   | OverflowError: value out of int32 range
backend-1   | 
backend-1   | The above exception was the direct cause of the following exception:
backend-1   | 
backend-1   | Traceback (most recent call last):
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 550, in _prepare_and_execute
backend-1   |     self._rows = deque(await prepared_stmt.fetch(*parameters))
backend-1   |                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 177, in fetch
backend-1   |     data = await self.__bind_execute(args, 0, timeout)
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 268, in __bind_execute
backend-1   |     data, status, _ = await self.__do_execute(
backend-1   |                       ^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 257, in __do_execute
backend-1   |     return await executor(protocol)
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "asyncpg/protocol/protocol.pyx", line 184, in bind_execute
backend-1   |   File "asyncpg/protocol/prepared_stmt.pyx", line 204, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
backend-1   | asyncpg.exceptions.DataError: invalid input for query argument $1: 231881100250 (value out of int32 range)
backend-1   | 
backend-1   | The above exception was the direct cause of the following exception:
backend-1   | 
backend-1   | Traceback (most recent call last):
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
backend-1   |     self.dialect.do_execute(
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
backend-1   |     cursor.execute(statement, parameters)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
backend-1   |     self._adapt_connection.await_(
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
backend-1   |     return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
backend-1   |     value = await result
backend-1   |             ^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
backend-1   |     self._handle_exception(error)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
backend-1   |     self._adapt_connection._handle_exception(error)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
backend-1   |     raise translated_error from error
backend-1   | sqlalchemy.dialects.postgresql.asyncpg.AsyncAdapt_asyncpg_dbapi.Error: <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: 231881100250 (value out of int32 range)
backend-1   | 
backend-1   | The above exception was the direct cause of the following exception:
backend-1   | 
backend-1   | Traceback (most recent call last):
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/protocols/http/httptools_impl.py", line 409, in run_asgi
backend-1   |     result = await app(  # type: ignore[func-returns-value]
backend-1   |              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/middleware/proxy_headers.py", line 60, in __call__
backend-1   |     return await self.app(scope, receive, send)
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/applications.py", line 1135, in __call__
backend-1   |     await super().__call__(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/applications.py", line 107, in __call__
backend-1   |     await self.middleware_stack(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/errors.py", line 186, in __call__
backend-1   |     raise exc
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/errors.py", line 164, in __call__
backend-1   |     await self.app(scope, receive, _send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/cors.py", line 93, in __call__
backend-1   |     await self.simple_response(scope, receive, send, request_headers=headers)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/cors.py", line 144, in simple_response
backend-1   |     await self.app(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/middleware/exceptions.py", line 63, in __call__
backend-1   |     await wrap_app_handling_exceptions(self.app, conn)(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
backend-1   |     raise exc
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
backend-1   |     await app(scope, receive, sender)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/middleware/asyncexitstack.py", line 18, in __call__
backend-1   |     await self.app(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 716, in __call__
backend-1   |     await self.middleware_stack(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 736, in app
backend-1   |     await route.handle(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/routing.py", line 290, in handle
backend-1   |     await self.app(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 115, in app
backend-1   |     await wrap_app_handling_exceptions(app, request)(scope, receive, send)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 53, in wrapped_app
backend-1   |     raise exc
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/starlette/_exception_handler.py", line 42, in wrapped_app
backend-1   |     await app(scope, receive, sender)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 101, in app
backend-1   |     response = await f(request)
backend-1   |                ^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 355, in app
backend-1   |     raw_response = await run_endpoint_function(
backend-1   |                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/fastapi/routing.py", line 243, in run_endpoint_function
backend-1   |     return await dependant.call(**values)
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/api/home/routes.py", line 186, in update_country_route
backend-1   |     return await update_country(
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/api/home/logics.py", line 418, in update_country
backend-1   |     await db.commit()
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 1000, in commit
backend-1   |     await greenlet_spawn(self.sync_session.commit)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
backend-1   |     result = context.switch(value)
backend-1   |              ^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2030, in commit
backend-1   |     trans.commit(_to_root=True)
backend-1   |   File "<string>", line 2, in commit
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
backend-1   |     ret_value = fn(self, *arg, **kw)
backend-1   |                 ^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1311, in commit
backend-1   |     self._prepare_impl()
backend-1   |   File "<string>", line 2, in _prepare_impl
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
backend-1   |     ret_value = fn(self, *arg, **kw)
backend-1   |                 ^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1286, in _prepare_impl
backend-1   |     self.session.flush()
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4331, in flush
backend-1   |     self._flush(objects)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4466, in _flush
backend-1   |     with util.safe_reraise():
backend-1   |          ^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 224, in __exit__
backend-1   |     raise exc_value.with_traceback(exc_tb)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4427, in _flush
backend-1   |     flush_context.execute()
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 466, in execute
backend-1   |     rec.execute(self)
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 642, in execute
backend-1   |     util.preloaded.orm_persistence.save_obj(
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 85, in save_obj
backend-1   |     _emit_update_statements(
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 912, in _emit_update_statements
backend-1   |     c = connection.execute(
backend-1   |         ^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
backend-1   |     return meth(
backend-1   |            ^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
backend-1   |     return connection._execute_clauseelement(
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1641, in _execute_clauseelement
backend-1   |     ret = self._execute_context(
backend-1   |           ^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
backend-1   |     return self._exec_single_context(
backend-1   |            ^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1   |   File "/app

