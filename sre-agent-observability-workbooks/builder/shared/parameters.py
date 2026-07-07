"""
The shared parameter block placed on every workbook page.

Exposes the tenant-wide filtering parameters required by the suite:
management group, subscription, resource group, region, environment,
workload/application, owner team, business criticality, resource type,
time range, severity, and recommendation status. Categorical parameters use
static JSON dropdowns; scope parameters use the built-in subscription picker;
the Log Analytics workspace uses an Azure Resource Graph-backed resource picker.
"""

from __future__ import annotations

import json
from typing import Any


def _dropdown(
    name: str,
    label: str,
    options: list[str],
    *,
    default: str,
    multi: bool = False,
) -> dict[str, Any]:
    json_data = [
        {"value": opt, "label": opt, "selected": (opt == default)} for opt in options
    ]
    item: dict[str, Any] = {
        "id": f"param-{name.lower()}",
        "version": "KqlParameterItem/1.0",
        "name": name,
        "label": label,
        "type": 2,
        "isRequired": False,
        # Static value list via jsonData (resolves instantly). Using query +
        # queryType 8 instead makes the workbook treat the list as a query to
        # execute, which can hang as "<query pending>".
        "typeSettings": {"additionalResourceOptions": []},
        "jsonData": json.dumps(json_data),
        "value": default,
    }
    if multi:
        item.update({"multiSelect": True, "quote": "'", "delimiter": ","})
    return item


def _text(name: str, label: str, default: str) -> dict[str, Any]:
    return {
        "id": f"param-{name.lower()}",
        "version": "KqlParameterItem/1.0",
        "name": name,
        "label": label,
        "type": 1,
        "isRequired": False,
        "value": default,
    }


def _workspace_picker(name: str, label: str) -> dict[str, Any]:
    """Azure Resource Graph-backed resource picker for the central workspace.

    Resolves to a Log Analytics workspace resource ID so Log Analytics tiles
    (which bind ``crossComponentResources`` to ``{LogAnalyticsWorkspaceId}``)
    have a valid target. The operator selects their workspace from the list
    instead of editing a placeholder resource-ID string.
    """
    return {
        "id": f"param-{name.lower()}",
        "version": "KqlParameterItem/1.0",
        "name": name,
        "label": label,
        "type": 5,  # resource picker
        "isRequired": True,
        "query": (
            "resources "
            "| where type =~ 'microsoft.operationalinsights/workspaces' "
            "| project value = id, label = name, selected = false "
            "| order by label asc"
        ),
        "crossComponentResources": ["value::all"],
        "queryType": 1,  # Azure Resource Graph
        "typeSettings": {
            "additionalResourceOptions": [],
            "showDefault": False,
        },
    }


def parameters_item() -> dict[str, Any]:
    """The type-9 parameters tile shared by every workbook page."""
    params: list[dict[str, Any]] = [
        {
            "id": "param-timerange",
            "version": "KqlParameterItem/1.0",
            "name": "TimeRange",
            "label": "Time range",
            "type": 4,
            "isRequired": True,
            "typeSettings": {
                "selectableValues": [
                    {"durationMs": 3600000},
                    {"durationMs": 14400000},
                    {"durationMs": 43200000},
                    {"durationMs": 86400000},
                    {"durationMs": 172800000},
                    {"durationMs": 604800000},
                    {"durationMs": 2592000000},
                ],
                "allowCustom": True,
            },
            "value": {"durationMs": 604800000},
        },
        {
            "id": "param-subscriptions",
            "version": "KqlParameterItem/1.0",
            "name": "Subscriptions",
            "label": "Subscriptions",
            "type": 6,  # subscription picker
            "isRequired": True,
            "multiSelect": True,
            "quote": "'",
            "delimiter": ",",
            "typeSettings": {
                "additionalResourceOptions": ["value::all"],
                "includeAll": True,
                "showDefault": False,
            },
            "value": ["value::all"],
        },
        # The central Log Analytics workspace that holds SREAgentRecommendations_CL
        # and tenant telemetry. ARG-backed resource picker so the operator selects
        # their workspace (no placeholder resource ID to hand-edit).
        _workspace_picker(
            "LogAnalyticsWorkspaceId",
            "Log Analytics workspace",
        ),
        _text("ManagementGroup", "Management group", "*"),
        _text("ResourceGroup", "Resource group", "*"),
        _text("Region", "Region", "*"),
        _dropdown(
            "Environment",
            "Environment",
            ["All", "Production", "Pre-Production", "Development", "Test", "Sandbox"],
            default="All",
        ),
        _text("Workload", "Workload / application", "*"),
        _text("OwnerTeam", "Owner team", "*"),
        _dropdown(
            "BusinessCriticality",
            "Business criticality",
            ["All", "Mission Critical", "Business Critical", "High", "Medium", "Low"],
            default="All",
        ),
        _text("ResourceType", "Resource type", "*"),
        _dropdown(
            "Severity",
            "Severity",
            [
                "All",
                "Sev0 - Critical",
                "Sev1 - High",
                "Sev2 - Medium",
                "Sev3 - Low",
                "Sev4 - Informational",
            ],
            default="All",
        ),
        _dropdown(
            "RecommendationStatus",
            "Recommendation status",
            [
                "Active",
                "All",
                "New",
                "Pending approval",
                "Approved",
                "In progress",
                "Applied",
                "Validated",
                "Rejected",
                "Suppressed",
                "Expired",
                "Failed",
                "Rolled back",
            ],
            default="Active",
        ),
    ]

    return {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": params,
        },
        "name": "shared-parameters",
    }
