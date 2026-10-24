from .config import Settings
from .db import Database
from .max_client import MAXClient, MockMAX
from .ml import HTTPML, StubML
from .models import Identity, State


class Runtime:
    def __init__(self, settings: Settings, *, database=None, ml=None, transport=None):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = database or Database(settings.database_url)
        self.db.sessions.configure(info={"data_dir": settings.data_dir})
        self.ml = ml or (StubML() if settings.ml_mode == "stub" else HTTPML(settings))
        self.transport = transport or (MockMAX() if settings.transport == "mock" else MAXClient(settings))

    def bootstrap(self):
        with self.db.transaction(write_lock=True) as session:
            clock = session.get(State, "clock")
            if self.settings.environment == "production" and clock and clock.value.get("now"):
                raise ValueError("Reset the persisted demo clock before switching to production")
            if not session.get(Identity, "bootstrap"):
                session.add(
                    Identity(
                        id="bootstrap",
                        name="Управление доступом",
                        roles=[],
                        manage_access=True,
                        branch_codes=[],
                    )
                )

    def close(self):
        self.ml.close()
        self.transport.close()
        self.db.engine.dispose()
