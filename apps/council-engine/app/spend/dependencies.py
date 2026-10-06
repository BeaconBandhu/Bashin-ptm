"""A cached, process-wide SpendTracker — same `@lru_cache` singleton
pattern as app/config.py's get_settings(). Needed because the
ai_spend_limit guardrail is attached to a node TYPE at registration time
(the @register_node_type decorator runs at module import), before any
per-request wiring exists — unlike the mock bank/CRM/payment-rail clients,
which are injected into node INSTANCES later. Constructing an
AsyncIOMotorClient here is safe at import time: motor connects lazily on
first operation, not in __init__.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.spend.tracker import InMemorySpendTracker, MongoSpendTracker, SpendTracker


@lru_cache
def get_spend_tracker() -> SpendTracker:
    settings = get_settings()
    if settings.has_mongodb:
        return MongoSpendTracker(settings.mongodb_uri, settings.mongodb_db_name)
    return InMemorySpendTracker()
