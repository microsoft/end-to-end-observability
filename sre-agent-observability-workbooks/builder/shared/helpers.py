"""
Reusable Azure Workbook item builders.

Every workbook in the SRE Agent Observability suite is assembled from these
helpers so that structure, scoping, and the shared Top 10 pattern are identical
across all ten pages. Each helper returns a JSON-serializable dict that is a
single Azure Workbook "item" (https://github.com/microsoft/Application-Insights-Workbooks).

Scoping conventions:
  * Azure Resource Graph tiles scope to the ``{Subscriptions}`` parameter, so
    they work tenant-wide regardless of any Log Analytics workspace.
  * Log Analytics tiles scope to the ``{LogAnalyticsWorkspaceId}`` parameter,
    which defaults to a documented placeholder the operator must supply.
"""

from __future__ import annotations

import itertools
import re
from typing import Any

# Standard cross-component bindings (workbook runtime parameters).
ARG_SCOPE = ["{Subscriptions}"]
LA_SCOPE = ["{LogAnalyticsWorkspaceId}"]

# Azure Resource Graph rows expose only the subscription GUID (``subscriptionId``);
# the friendly subscription name lives in ``resourcecontainers``. Insert this join
# into an ARG query before its summarize/project so tiles group and display by the
# ``Subscription`` name instead of the GUID. Use ``Subscription`` in place of
# ``subscriptionId`` downstream.
SUB_NAME_JOIN = (
    "| join kind=leftouter (resourcecontainers\n"
    "    | where type =~ 'microsoft.resources/subscriptions'\n"
    "    | project subscriptionId, Subscription = name) on subscriptionId\n"
)

# Azure Resource Graph exposes only the role-definition GUID
# (``properties.roleDefinitionId``) on a role assignment; the friendly role name
# lives in ``authorizationresources`` role *definitions*. Insert this snippet into a
# roleassignments query (which has already extended ``roleDefinitionId`` or immediately
# after the ``where type =~ 'microsoft.authorization/roleassignments'`` line) to resolve
# the GUID to ``RoleName = properties.roleName``. Never print the role GUID downstream -
# filter and display on ``RoleName`` instead.
ROLE_NAME_JOIN = (
    "| extend roleDefinitionId = tolower(tostring(properties.roleDefinitionId))\n"
    "| join kind=leftouter (authorizationresources\n"
    "    | where type =~ 'microsoft.authorization/roledefinitions'\n"
    "    | project roleDefinitionId = tolower(id), RoleName = tostring(properties.roleName)) "
    "on roleDefinitionId\n"
)

# Default friendly empty-state message. Attach to every data tile so an empty result
# reads as "nothing to flag / adjust scope" rather than a broken panel.
DEFAULT_NO_DATA = (
    "No data for the current filters and time range. Adjust the scope (Subscriptions / "
    "workspace / time), or confirm the data source is connected."
)

_counter = itertools.count(1)


def _auto_name(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}"


def reset_names() -> None:
    """Reset the auto-name counter so each workbook gets stable item names."""
    global _counter
    _counter = itertools.count(1)


def markdown(text: str, name: str | None = None) -> dict[str, Any]:
    """A text/markdown tile. Use for guidance and 'how to act' sections."""
    return {
        "type": 1,
        "content": {"json": text},
        "name": name or _auto_name("md"),
    }


def arg_query(
    title: str,
    query: str,
    *,
    visualization: str = "table",
    size: int = 0,
    name: str | None = None,
    grid_settings: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An Azure Resource Graph tile (tenant-wide inventory / governance)."""
    content: dict[str, Any] = {
        "version": "KqlItem/1.0",
        "query": query,
        "size": size,
        "title": title,
        "queryType": 1,
        "resourceType": "microsoft.resourcegraph/resources",
        "crossComponentResources": ARG_SCOPE,
        "visualization": visualization,
    }
    if grid_settings:
        content["gridSettings"] = grid_settings
    if extra:
        content.update(extra)
    return {"type": 3, "content": content, "name": name or _auto_name("arg")}


def la_query(
    title: str,
    query: str,
    *,
    visualization: str = "table",
    size: int = 0,
    name: str | None = None,
    grid_settings: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Log Analytics / Application Insights KQL tile bound to the workspace
    placeholder parameter. Used for logs, alerts, telemetry, recommendations."""
    content: dict[str, Any] = {
        "version": "KqlItem/1.0",
        "query": query,
        "size": size,
        "title": title,
        "queryType": 0,
        "resourceType": "microsoft.operationalinsights/workspaces",
        "crossComponentResources": LA_SCOPE,
        "visualization": visualization,
    }
    if grid_settings:
        content["gridSettings"] = grid_settings
    if extra:
        content.update(extra)
    return {"type": 3, "content": content, "name": name or _auto_name("la")}


def kpi_tiles(title_col: str, value_col: str, *, subtitle_col: str | None = None) -> dict[str, Any]:
    """tileSettings for a KPI scorecard: one tile per row, big colored number."""
    left = {
        "columnMatch": value_col,
        "formatter": 12,  # big number
        "formatOptions": {"palette": "auto"},
        "numberFormat": {
            "unit": 17,
            "options": {"style": "decimal", "maximumFractionDigits": 0},
        },
    }
    tile: dict[str, Any] = {
        "titleContent": {"columnMatch": title_col, "formatter": 1},
        "leftContent": left,
        "showBorder": True,
        "size": "auto",
    }
    if subtitle_col:
        tile["subtitleContent"] = {"columnMatch": subtitle_col, "formatter": 1}
    return {"tileSettings": tile}


def kpi_tiles_thresholds(
    title_col: str,
    value_col: str,
    rules: list[dict[str, Any]],
    *,
    subtitle_col: str | None = None,
) -> dict[str, Any]:
    """Threshold-colored variant of :func:`kpi_tiles`. The big number is colored by
    ``rules`` (formatter 12 + thresholdsGrid) so a hero KPI turns red/orange/green by
    rule instead of ``palette: auto``. ``rules`` is a list of
    {operator, thresholdValue, representation, text} entries (worst -> best, with a
    trailing ``Default`` rule)."""
    left = {
        "columnMatch": value_col,
        "formatter": 12,  # big number
        "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": rules},
        "numberFormat": {
            "unit": 17,
            "options": {"style": "decimal", "maximumFractionDigits": 0},
        },
    }
    tile: dict[str, Any] = {
        "titleContent": {"columnMatch": title_col, "formatter": 1},
        "leftContent": left,
        "showBorder": True,
        "size": "auto",
    }
    if subtitle_col:
        tile["subtitleContent"] = {"columnMatch": subtitle_col, "formatter": 1}
    return {"tileSettings": tile}


# Color thresholds for a computed posture string (worst -> best).
_POSTURE_COLORS = [
    {"operator": "==", "thresholdValue": "CRITICAL", "representation": "red", "text": "{0}"},
    {"operator": "==", "thresholdValue": "DEGRADED", "representation": "orange", "text": "{0}"},
    {"operator": "==", "thresholdValue": "ELEVATED", "representation": "yellow", "text": "{0}"},
    {"operator": "Default", "thresholdValue": None, "representation": "green", "text": "{0}"},
]


def status_banner(status_col: str, count_col: str, subtitle_col: str | None = None) -> dict[str, Any]:
    """tileSettings for a hero posture banner: a big count with the posture word
    colored red/orange/yellow/green by status."""
    tile: dict[str, Any] = {
        "titleContent": {
            "columnMatch": status_col,
            "formatter": 18,
            "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": _POSTURE_COLORS},
        },
        "leftContent": {
            "columnMatch": count_col,
            "formatter": 12,
            "formatOptions": {"palette": "auto"},
            "numberFormat": {"unit": 17, "options": {"style": "decimal", "maximumFractionDigits": 0}},
        },
        "showBorder": True,
        "size": "full",
    }
    if subtitle_col:
        tile["subtitleContent"] = {"columnMatch": subtitle_col, "formatter": 1}
    return {"tileSettings": tile}


def icon_grid(column: str, mapping: list[tuple[str, Any, str]]) -> dict[str, Any]:
    """gridSettings that render a column with status icons (traffic lights).
    mapping is a list of (operator, thresholdValue, iconRepresentation)."""
    grid = [
        {"operator": op, "thresholdValue": val, "representation": icon, "text": "{0}"}
        for op, val, icon in mapping
    ]
    return {
        "formatters": [
            {
                "columnMatch": column,
                "formatter": 18,
                "formatOptions": {"thresholdsOptions": "icons", "thresholdsGrid": grid},
            }
        ]
    }


def heatmap_grid(column: str, palette: str = "red") -> dict[str, Any]:
    """gridSettings that render a numeric column as a colored heatmap bar
    (e.g. alert age, so old items stand out)."""
    return {
        "formatters": [
            {
                "columnMatch": column,
                "formatter": 8,  # heatmap / bar
                "formatOptions": {"palette": palette},
            }
        ]
    }


def thresholds_grid(column: str, rules: list[dict[str, Any]]) -> dict[str, Any]:
    """gridSettings that color a column by threshold rules. Each rule is
    {operator, thresholdValue, representation, text}."""
    return {
        "formatters": [
            {
                "columnMatch": column,
                "formatter": 18,  # thresholds
                "formatOptions": {"thresholdsOptions": "colors", "thresholdsGrid": rules},
            }
        ]
    }


def link_grid(*url_columns: str) -> dict[str, Any]:
    """Grid settings that render the named columns as clickable drilldown links
    (evidence, runbook, incident, PR, work item)."""
    formatters = [
        {
            "columnMatch": col,
            "formatter": 7,  # link formatter
            "formatOptions": {"linkTarget": "Url", "linkLabel": col},
        }
        for col in url_columns
    ]
    return {"formatters": formatters}


def resource_link_grid(id_column: str, label: str = "Open") -> dict[str, Any]:
    """Grid settings that turn a full ``resourceId`` column into a click-through that
    opens that resource's blade in the Azure portal (formatter 7, linkTarget
    ``Resource``). Use for any row tied to a real Azure resource so operators can jump
    straight to it. Merge the returned ``formatters`` with other grid formatters as
    needed."""
    return {
        "formatters": [
            {
                "columnMatch": id_column,
                "formatter": 7,  # link formatter
                "formatOptions": {"linkTarget": "Resource", "linkLabel": label},
            }
        ]
    }


def with_no_data(item: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    """Attach a friendly ``noDataMessage`` to a query tile so an empty result reads as
    'nothing to flag / adjust scope' instead of a blank panel. Returns the same item
    for chaining."""
    content = item.setdefault("content", {})
    content["noDataMessage"] = message or DEFAULT_NO_DATA
    return item


def with_export(
    item: dict[str, Any], field_column: str, parameter_name: str, default_value: str = ""
) -> dict[str, Any]:
    """Make a grid clickable for an **entity-centric pivot**: clicking a row exports
    ``field_column`` into the workbook parameter ``parameter_name``. Downstream tiles
    filter on ``{parameter_name}``; an empty default means 'no selection -> show all'.
    Returns the item for chaining."""
    content = item.setdefault("content", {})
    content["exportFieldName"] = field_column
    content["exportParameterName"] = parameter_name
    content["exportDefaultValue"] = default_value
    return item


def pivot_params(specs: list[tuple[str, str]], *, name: str | None = None) -> dict[str, Any]:
    """A hidden type-9 parameter block that backs an entity-centric pivot. ``specs`` is a
    list of ``(parameter_name, label)`` pairs; each becomes an empty text parameter that
    :func:`with_export` targets and downstream tiles filter on. Kept separate from the 13
    shared parameters so it never interferes with them."""
    params = [
        {
            "id": _auto_name("pivot"),
            "version": "KqlParameterItem/1.0",
            "name": pname,
            "label": label,
            "type": 1,  # text
            "isHiddenWhenLocked": True,
            "value": "",
        }
        for pname, label in specs
    ]
    return {
        "type": 9,
        "content": {"version": "KqlParameterItem/1.0", "parameters": params, "style": "pills"},
        "name": name or _auto_name("pivotparams"),
    }


def divider(name: str | None = None) -> dict[str, Any]:
    return markdown("\n---\n", name=name or _auto_name("hr"))


def section(heading: str, guidance: str, *tiles: dict[str, Any]) -> list[dict[str, Any]]:
    """A page section = a guidance tile ('what this shows / how to act') followed
    by zero or more query/placeholder tiles. Keeps every page self-explanatory."""
    items = [markdown(f"## {heading}\n\n{guidance}", name=_auto_name("sec"))]
    items.extend(tiles)
    return items


def placeholder(title: str, body: str) -> dict[str, Any]:
    """A clearly-marked placeholder tile for data sources that require
    tenant-specific configuration (table names, action groups, CMDB, Defender,
    Entra, etc.) before they can render real data."""
    text = (
        f"> **Placeholder - {title}**\n>\n"
        f"> {body}\n>\n"
        "> Supply the tenant-specific data source, then replace this tile with the "
        "documented query. See `docs/data-sources.md`."
    )
    return markdown(text, name=_auto_name("ph"))


def _tab_value(label: str) -> str:
    """Stable, parameter-safe token for a tab label (e.g. 'Access & Lifecycle' -> 'accesslifecycle')."""
    return re.sub(r"[^a-z0-9]+", "", label.lower()) or "tab"


def tab_group(title: str, tabs: list[tuple[str, list[dict[str, Any]]]], *, name: str | None = None) -> dict[str, Any]:
    """Wrap page content into a single Workbook **tabbed group** so a long page becomes
    a compact tab strip (e.g. Overview | Threats | Identity | Exposure | ...).

    ``tabs`` is a list of ``(label, items)`` pairs; each ``items`` is the list of
    workbook items shown when that tab is selected. Rendered as a links/tabs selector
    (type 11) driving a hidden text parameter, plus one conditionally-visible subgroup
    (type 12) per tab. ``title`` seeds the tab parameter name so multiple tab groups on
    one page stay independent."""
    slug = _tab_value(title)
    param = f"tab_{slug}"
    first_value = _tab_value(tabs[0][0]) if tabs else "tab"

    param_block = {
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [
                {
                    "id": _auto_name("tabparam"),
                    "version": "KqlParameterItem/1.0",
                    "name": param,
                    "type": 1,  # text
                    "isHiddenWhenLocked": True,
                    "value": first_value,
                }
            ],
            "style": "pills",
        },
        "name": _auto_name("tabparamblock"),
    }

    selector = {
        "type": 11,
        "content": {
            "version": "LinkItem/1.0",
            "style": "tabs",
            "links": [
                {
                    "id": _auto_name("tablink"),
                    "cellValue": param,
                    "linkTarget": "parameter",
                    "linkLabel": label,
                    "subTarget": _tab_value(label),
                    "style": "link",
                }
                for label, _ in tabs
            ],
        },
        "name": _auto_name("tabselector"),
    }

    children: list[dict[str, Any]] = [param_block, selector]
    for label, items in tabs:
        children.append(
            {
                "type": 12,
                "conditionalVisibility": {
                    "parameterName": param,
                    "comparison": "isEqualTo",
                    "value": _tab_value(label),
                },
                "content": {
                    "version": "NotebookGroup/1.0",
                    "groupType": "editable",
                    "items": items,
                },
                "name": _auto_name("tabcontent"),
            }
        )

    return {
        "type": 12,
        "content": {"version": "NotebookGroup/1.0", "groupType": "editable", "items": children},
        "name": name or _auto_name("tabgroup"),
    }
