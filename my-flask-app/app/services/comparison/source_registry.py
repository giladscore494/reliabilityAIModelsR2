"""Re-export of the comparison V2 official source registry.

The registry lives in ``app.services.comparison_v2.source_registry``; this
module keeps the path referenced by the V2 spec importable.
"""

from app.services.comparison_v2.source_registry import (  # noqa: F401
    OFFICIAL_SOURCE_REGISTRY,
    SOURCE_REGISTRY_VERSION,
    check_official_url,
    is_allowed_official_url,
)
