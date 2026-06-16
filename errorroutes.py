File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1660, in do_init
backend-1  |     self._generate_backref()
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 2144, in _generate_backref
backend-1  |     self._add_reverse_property(self.back_populates)
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/relationships.py", line 1591, in _add_reverse_property
backend-1  |     other = self.mapper.get_property(key, _configure_mappers=False)
backend-1  |             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend-1  |   File "/app/.venv/lib/python3.12/site-packages/sqlalchemy/orm/mapper.py", line 2533, in get_property
backend-1  |     raise sa_exc.InvalidRequestError(
backend-1  | sqlalchemy.exc.InvalidRequestError: Mapper 'Mapper[Country(countries)]' has no property 'offices'.  If this property was indicated from other mappers or configure events, ensure registry.configure() has been called.
