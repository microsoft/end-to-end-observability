"""Authentication helpers.

Discovery is read-only and only requires the `Reader` role at the chosen
scope (subscription or management group). We use DefaultAzureCredential
so the same code works for developers (az login), CI (workload identity),
and customer environments (managed identity).
"""

from __future__ import annotations

from azure.identity import DefaultAzureCredential


def get_credential() -> DefaultAzureCredential:
    """Return a DefaultAzureCredential.

    Order of resolution (per azure-identity docs):
      1. Environment variables
      2. Workload identity / managed identity
      3. Azure CLI
      4. Azure PowerShell / Developer CLI
    """
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)
