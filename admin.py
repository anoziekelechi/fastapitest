
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
❌ Password must contain at least one special character (@#&_-+)

Password: 
Confirm password: 
WARNING:passlib.handlers.bcrypt:(trapped) error reading bcrypt version
Traceback (most recent call last):
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 620, in _load_backend_mixin
    version = _bcrypt.__about__.__version__
              ^^^^^^^^^^^^^^^^^
AttributeError: module 'bcrypt' has no attribute '__about__'
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
  File "/app/api/admin/script.py", line 265, in create_admin
    hashed_password=hash_password(password),
                    ^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/api/core/auth.py", line 41, in hash_password
    return pwd_context.hash(password)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/context.py", line 2258, in hash
    return record.hash(secret, **kwds)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 779, in hash
    self.checksum = self._calc_checksum(secret)
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 591, in _calc_checksum
    self._stub_requires_backend()
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2254, in _stub_requires_backend
    cls.set_backend()
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2156, in set_backend
    return owner.set_backend(name, dryrun=dryrun)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2163, in set_backend
    return cls.set_backend(name, dryrun=dryrun)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2188, in set_backend
    cls._set_backend(name, dryrun)
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2311, in _set_backend
    super(SubclassBackendMixin, cls)._set_backend(name, dryrun)
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 2224, in _set_backend
    ok = loader(**kwds)
         ^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 626, in _load_backend_mixin
    return mixin_cls._finalize_backend_mixin(name, dryrun)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 421, in _finalize_backend_mixin
    if detect_wrap_bug(IDENT_2A):
       ^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 380, in detect_wrap_bug
    if verify(secret, bug_hash):
       ^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/utils/handlers.py", line 792, in verify
    return consteq(self._calc_checksum(secret), chk)
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/app/.venv/lib/python3.12/site-packages/passlib/handlers/bcrypt.py", line 655, in _calc_checksum
    hash = _bcrypt.hashpw(secret, config)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ValueError: password cannot be longer than 72 bytes, truncate manually if necessary (e.g. my_password[:72])
