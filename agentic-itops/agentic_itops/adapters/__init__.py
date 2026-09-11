"""Source adapters.

Each adapter answers two questions: *can I parse this payload?* and
*what is the canonical envelope for it?* Adapters are registered in
priority order and the first one that claims a payload wins.

Adding a source means adding one file here. Nothing else in the system
changes -- that is the point of the envelope.
"""

from __future__ import annotations

from .base import Adapter, AdapterRegistry, registry
from .azure_monitor import AzureMonitorAdapter
from .datadog import DatadogAdapter
from .prometheus import PrometheusAdapter
from .servicenow import ServiceNowAdapter
from .splunk import SplunkAdapter

__all__ = [
    "Adapter",
    "AdapterRegistry",
    "registry",
    "AzureMonitorAdapter",
    "DatadogAdapter",
    "PrometheusAdapter",
    "ServiceNowAdapter",
    "SplunkAdapter",
]
