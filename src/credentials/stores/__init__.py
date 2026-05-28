"""Built-in credential stores.

Each store implements
:class:`~src.credentials.ports.CredentialStorePort`. Stores are
plain value objects -- not :class:`~src.components.RobotComponent`
instances -- so the builder can construct them eagerly during the
substitution pass, before any robot lifecycle is running.
"""

from src.credentials.stores.env import EnvCredentialStore
from src.credentials.stores.process_env import ProcessEnvCredentialStore

__all__ = ["EnvCredentialStore", "ProcessEnvCredentialStore"]
