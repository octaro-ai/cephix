"""Model catalog: read-side view of model specs and pricing.

Boot category: :attr:`~src.components.ComponentCategory.UTILITY`
(boot priority 5). Off-bus, sync, consumed by reference-injection
into one or two consumers (today: optionally an actor that wants
catalog-driven cost computation; tomorrow: the LLMKernel).

Public surface:

- :class:`ModelCatalog` -- the component.
- :class:`ModelCatalogPort`, :class:`ModelDataSource` -- the ABCs
  consumers and sources implement.
- :class:`ModelSpec`, :class:`ModelPricing` -- the value types
  flowing through the port.
- :class:`LLMPriceKitSource` -- the default data source (wraps the
  ``llmprice`` lib).
"""

from src.utility.model_catalog.catalog import ModelCatalog
from src.utility.model_catalog.ports import ModelCatalogPort, ModelDataSource
from src.utility.model_catalog.sources import LLMPriceKitSource
from src.utility.model_catalog.types import ModelPricing, ModelSpec

__all__ = [
    "LLMPriceKitSource",
    "ModelCatalog",
    "ModelCatalogPort",
    "ModelDataSource",
    "ModelPricing",
    "ModelSpec",
]
