[+] Running 3/3
 ✔ Container ecommerce-redis-1    Running                                                                                                               0.0s 
 ✔ Container ecommerce-db-1       Running                                                                                                               0.0s 
 ✔ Container ecommerce-backend-1  Created                                                                                                               0.0s 
Attaching to backend-1
backend-1  | INFO:     Will watch for changes in these directories: ['/app']
backend-1  | INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
backend-1  | INFO:     Started reloader process [1] using WatchFiles
backend-1  | Process SpawnProcess-1:
backend-1  | Traceback (most recent call last):
backend-1  |   File "/usr/local/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
backend-1  |     self.run()
backend-1  |   File "/usr/local/lib/python3.12/multiprocessing/process.py", line 108, in run
backend-1  |     self._target(*self._args, **self._kwargs)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/_subprocess.py", line 80, in subprocess_started
backend-1  |     target(sockets=sockets)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/server.py", line 67, in run
backend-1  |     return asyncio_run(self.serve(sockets=sockets), loop_factory=self.config.get_loop_factory())
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/usr/local/lib/python3.12/asyncio/runners.py", line 195, in run
backend-1  |     return runner.run(main)
backend-1  |            ^^^^^^^^^^^^^^^^
backend-1  |   File "/usr/local/lib/python3.12/asyncio/runners.py", line 118, in run
backend-1  |     return self._loop.run_until_complete(task)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "uvloop/loop.pyx", line 1518, in uvloop.loop.Loop.run_until_complete
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/server.py", line 71, in serve
backend-1  |     await self._serve(sockets)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/server.py", line 78, in _serve
backend-1  |     config.load()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/config.py", line 439, in load
backend-1  |     self.loaded_app = import_from_string(self.app)
backend-1  |                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/uvicorn/importer.py", line 19, in import_from_string
backend-1  |     module = importlib.import_module(module_str)
backend-1  |              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/usr/local/lib/python3.12/importlib/__init__.py", line 90, in import_module
backend-1  |     return _bootstrap._gcd_import(name[level:], package, level)
backend-1  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "<frozen importlib._bootstrap>", line 1387, in _gcd_import
backend-1  |   File "<frozen importlib._bootstrap>", line 1360, in _find_and_load
backend-1  |   File "<frozen importlib._bootstrap>", line 1331, in _find_and_load_unlocked
backend-1  |   File "<frozen importlib._bootstrap>", line 935, in _load_unlocked
backend-1  |   File "<frozen importlib._bootstrap_external>", line 999, in exec_module
backend-1  |   File "<frozen importlib._bootstrap>", line 488, in _call_with_frames_removed
backend-1  |   File "/app/api/main.py", line 7, in <module>
backend-1  |     from api.core.mail import mail_config
backend-1  |   File "/app/api/core/mail.py", line 8, in <module>
backend-1  |     MAIL_USERNAME=get_settings().mail_username,
backend-1  |                   ^^^^^^^^^^^^^^
backend-1  |   File "/app/api/core/settings.py", line 92, in get_settings
backend-1  |     return DevSettings()
backend-1  |            ^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/pydantic_settings/main.py", line 194, in __init__
backend-1  |     super().__init__(
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/pydantic/main.py", line 250, in __init__
backend-1  |     validated_self = self.__pydantic_validator__.validate_python(data, self_instance=self)
backend-1  |                      ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  | pydantic_core._pydantic_core.ValidationError: 2 validation errors for DevSettings
backend-1  |  POSTGRES_USER
backend-1  |   Field required [type=missing, input_value={'APP_MODE': 'development...': 'redis://redis:6379'}, input_type=dict]
backend-1  |     For further information visit https://errors.pydantic.dev/2.12/v/missing
backend-1  |  POSTGRES_DB
backend-1  |   Field required [type=missing, input_value={'APP_MODE': 'development...': 'redis://redis:6379'}, input_type=dict]
backend-1  |     For further information visit https://errors.pydantic.dev/2.12/v/missing
