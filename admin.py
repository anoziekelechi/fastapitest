
==================================================
  CREATE ADMIN USER
==================================================

ℹ️  Creating the FIRST admin account.
   Country selection is optional at this stage.
   You can assign a country later via the admin panel.

Email: kennedykelechijoseph@gmail.com
Surname: anozie
Othernames: kelechi

⚠️  No countries found in database.
   Skipping country selection for first admin.
   Run migrations to seed countries: alembic upgrade head
   You can update this later via the admin panel.

Password: 
Confirm password: 
Traceback (most recent call last):
  File "asyncpg/protocol/prepared_stmt.pyx", line 175, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
  File "asyncpg/protocol/codecs/base.pyx", line 251, in asyncpg.protocol.protocol.Codec.encode
  File "asyncpg/protocol/codecs/base.pyx", line 153, in asyncpg.protocol.protocol.Codec.encode_scalar
  File "asyncpg/pgproto/codecs/datetime.pyx", line 152, in asyncpg.pgproto.pgproto.timestamp_encode
TypeError: can't subtract offset-naive and offset-aware datetimes

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 550, in _prepare_and_execute
    self._rows = deque(await prepared_stmt.fetch(*parameters))
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 177, in fetch
    data = await self.__bind_execute(args, 0, timeout)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 268, in __bind_execute
    data, status, _ = await self.__do_execute(
                      ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/asyncpg/prepared_stmt.py", line 257, in __do_execute
    return await executor(protocol)
           ^^^^^^^^^^^^^^^^^^^^^^^^
  File "asyncpg/protocol/protocol.pyx", line 184, in bind_execute
  File "asyncpg/protocol/prepared_stmt.pyx", line 204, in asyncpg.protocol.protocol.PreparedStatementState._encode_bind_msg
asyncpg.exceptions.DataError: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.dialects.postgresql.asyncpg.AsyncAdapt_asyncpg_dbapi.Error: <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/app/api/admin/script.py", line 295, in <module>
    asyncio.run(create_admin())
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
    return runner.run(main)
           ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
    return self._loop.run_until_complete(task)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/asyncio/base_events.py", line 691, in run_until_complete
    return future.result()
           ^^^^^^^^^^^^^^^
  File "/app/api/admin/script.py", line 274, in create_admin
    await db.commit()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/ext/asyncio/session.py", line 1000, in commit
    await greenlet_spawn(self.sync_session.commit)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 203, in greenlet_spawn
    result = context.switch(value)
             ^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 2030, in commit
    trans.commit(_to_root=True)
  File "<string>", line 2, in commit
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1311, in commit
    self._prepare_impl()
  File "<string>", line 2, in _prepare_impl
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/state_changes.py", line 137, in _go
    ret_value = fn(self, *arg, **kw)
                ^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 1286, in _prepare_impl
    self.session.flush()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4331, in flush
    self._flush(objects)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4466, in _flush
    with util.safe_reraise():
         ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/langhelpers.py", line 224, in __exit__
    raise exc_value.with_traceback(exc_tb)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/session.py", line 4427, in _flush
    flush_context.execute()
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 466, in execute
    rec.execute(self)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/unitofwork.py", line 642, in execute
    util.preloaded.orm_persistence.save_obj(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 93, in save_obj
    _emit_insert_statements(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/persistence.py", line 1233, in _emit_insert_statements
    result = connection.execute(
             ^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1419, in execute
    return meth(
           ^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/sql/elements.py", line 527, in _execute_on_connection
    return connection._execute_clauseelement(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1641, in _execute_clauseelement
    ret = self._execute_context(
          ^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1846, in _execute_context
    return self._exec_single_context(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1986, in _exec_single_context
    self._handle_dbapi_exception(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 2363, in _handle_dbapi_exception
    raise sqlalchemy_exception.with_traceback(exc_info[2]) from e
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/base.py", line 1967, in _exec_single_context
    self.dialect.do_execute(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/engine/default.py", line 952, in do_execute
    cursor.execute(statement, parameters)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 585, in execute
    self._adapt_connection.await_(
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 132, in await_only
    return current.parent.switch(awaitable)  # type: ignore[no-any-return,attr-defined] # noqa: E501
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/util/_concurrency_py3k.py", line 196, in greenlet_spawn
    value = await result
            ^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 563, in _prepare_and_execute
    self._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 513, in _handle_exception
    self._adapt_connection._handle_exception(error)
  File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/dialects/postgresql/asyncpg.py", line 797, in _handle_exception
    raise translated_error from error
sqlalchemy.exc.DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: datetime.datetime(2026, 8, 6, 9, 41, 41,... (can't subtract offset-naive and offset-aware datetimes)
[SQL: INSERT INTO users (created_at, updated_at, group_id, country_id, surname, othernames, email, hashed_password, is_admin, disabled, payment_id, one_click, verified, date_verified) VALUES ($1::TIMESTAMP WITHOUT TIME ZONE, $2::TIMESTAMP WITHOUT TIME ZONE, $3::INTEGER, $4::INTEGER, $5::VARCHAR, $6::VARCHAR, $7::VARCHAR, $8::VARCHAR, $9::BOOLEAN, $10::BOOLEAN, $11::VARCHAR, $12::BOOLEAN, $13::BOOLEAN, $14::TIMESTAMP WITH TIME ZONE) RETURNING users.id]
[parameters: (datetime.datetime(2026, 8, 6, 9, 41, 41, 795556, tzinfo=datetime.timezone.utc), datetime.datetime(2026, 8, 6, 9, 41, 41, 795701, tzinfo=datetime.timezone.utc), None, None, 'ANOZIE', 'KELECHI', 'kennedykelechijoseph@gmail.com', '$2b$12$QIdVbHkAzkG7E88o74Wbwu9oWr32QUmCk7NJcBk0/lFq.o6.6dmTO', True, False, None, False, True, None)]
(Background on this error at: https://sqlalche.me/e/20/dbapi)

anoziekelechi@Anozies-MacBook-Pro Ecommerce % 
