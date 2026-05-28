"""Credential subsystem: stores, provider, ${KEY} substitution.

The credential subsystem owns *every* secret-resolution path in
cephix. It exists to stop the dotenv wildgrowth that otherwise
shows up in any non-trivial bot: each tool, each channel and each
LLM driver inventing its own ``os.environ.get`` ritual, plus a
hodgepodge of ``.env`` loaders sprinkled across modules.

Two layers:

**Stores** (:class:`CredentialStorePort`): passive sync data
holders. ``EnvCredentialStore`` parses a ``.env`` file once at
construction;
:class:`~src.credentials.stores.process_env.ProcessEnvCredentialStore`
delegates to ``os.environ``. New backends (Vault, KeePass, web
KeyStore, AWS Secrets Manager) plug in by implementing the same
:meth:`lookup` contract. Stores are *not* :class:`RobotComponent`s;
they're plain value objects the builder constructs and hands to
the provider.

**Provider** (:class:`CredentialProvider`): the
:attr:`~src.components.ComponentCategory.BUS_UTILITY`
:class:`~src.components.RobotComponent` that holds an ordered list
of stores, walks them on each lookup, and emits
:class:`~src.bus.messages.RobotAuditNote` events for every resolve
(without ever putting the resolved *value* on the bus). Components
that need credentials at runtime hold a constructor-injected
:class:`CredentialProviderPort` reference and call
:meth:`CredentialProvider.resolve` directly -- the bus only sees
the audit trail, never the secret.

Two consumers, one mechanism:

- The **builder** uses :func:`resolve_secrets` to substitute
  ``${KEY}`` patterns in the bot's YAML configuration *before*
  components are constructed. Fail-fast: a missing key aborts the
  build, no robot is born.
- **Components at runtime** (LLM actors, future tools, future
  channels) get the same provider injected by constructor
  convention (any constructor that declares a ``credentials``
  kwarg gets the active provider, identical to how the
  :class:`~src.utility.model_catalog.catalog.ModelCatalog` is wired).

Substitution syntax: ``${UPPER_SNAKE_CASE}``. Anything matching
``[A-Z][A-Z0-9_]*`` between ``${`` and ``}`` is a credential
reference. Use ``$$`` to escape a literal dollar sign in front of
a brace expression. See :mod:`src.credentials.substitution` for
the full grammar.
"""

from src.credentials.exceptions import (
    CredentialNotFound,
    CredentialStoreError,
)
from src.credentials.ports import (
    CredentialProviderPort,
    CredentialStorePort,
)
from src.credentials.provider import CredentialProvider
from src.credentials.stores.env import EnvCredentialStore
from src.credentials.stores.process_env import ProcessEnvCredentialStore
from src.credentials.substitution import (
    SECRET_REFERENCE_PATTERN,
    iter_secret_references,
    resolve_secrets,
)

__all__ = [
    "SECRET_REFERENCE_PATTERN",
    "CredentialNotFound",
    "CredentialProvider",
    "CredentialProviderPort",
    "CredentialStoreError",
    "CredentialStorePort",
    "EnvCredentialStore",
    "ProcessEnvCredentialStore",
    "iter_secret_references",
    "resolve_secrets",
]
