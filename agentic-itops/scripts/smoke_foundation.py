"""Run two bounded, synthetic foundation probes using the operator identity."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import AzureError
from azure.identity import AzureCliCredential
from openai import APIError


def check_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not parts.hostname.endswith(".services.ai.azure.com")
        or parts.username
        or parts.password
        or parts.port
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/api/projects/")
        or len(parts.path.split("/")) != 4
        or not parts.path.split("/")[-1]
    ):
        raise ValueError("Expected an HTTPS Azure Foundry project endpoint")
    return endpoint


def check_openai_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or not parts.hostname.endswith(".openai.azure.com")
        or parts.username
        or parts.password
        or parts.port
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Expected an HTTPS Azure OpenAI account endpoint")
    return endpoint.rstrip("/") + "/"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-endpoint", type=check_endpoint, required=True)
    parser.add_argument("--openai-endpoint", type=check_openai_endpoint, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        urlsplit(args.project_endpoint).hostname.removesuffix(".services.ai.azure.com")
        != urlsplit(args.openai_endpoint).hostname.removesuffix(".openai.azure.com")
    ):
        parser.error("Project and embedding endpoints must belong to the same Foundry account")
    report = {
        "scope": "foundation-only",
        "identity": "explicit Azure CLI operator; not consumer managed identity",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "maximum_model_requests": 2,
        "automatic_retries": 0,
        "results": {},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves prior attempts, including ambiguous failures.
    with args.output.open("x", encoding="utf-8") as receipt:
        json.dump(report, receipt, indent=2)
        receipt.flush()
        try:
            with AzureCliCredential(tenant_id=args.tenant_id) as credential:
                with AIProjectClient(
                    endpoint=args.project_endpoint, credential=credential, retry_total=0
                ) as project:
                    with project.get_openai_client(max_retries=0, timeout=60.0) as client:
                        response = client.responses.create(
                            model="gpt-5-6-luna",
                            input="Reply with exactly FOUNDATION_READY and nothing else.",
                            max_output_tokens=2048,
                            store=False,
                        )
                        if response.status != "completed" or response.output_text.strip() != "FOUNDATION_READY":
                            raise ValueError("Luna response did not complete with the expected readiness marker")
                        if response.usage is None:
                            raise ValueError("Luna response omitted usage accounting")
                        report["results"]["responses"] = {
                            "model": response.model,
                            "status": response.status,
                            "usage": response.usage.model_dump(mode="json"),
                            "readiness_marker_matched": True,
                        }
                        # Embeddings use the account API, not the project's Responses API.
                        with project.get_openai_client(
                            base_url=args.openai_endpoint + "openai/v1/",
                            max_retries=0,
                            timeout=60.0,
                        ) as embedding_client:
                            embedding = embedding_client.embeddings.create(
                                model="text-embedding-3-small",
                                input="Synthetic foundation readiness probe.",
                                dimensions=1536,
                            )
                        if len(embedding.data) != 1:
                            raise ValueError("Embedding response must contain exactly one vector")
                        vector = embedding.data[0].embedding
                        if len(vector) != 1536 or not all(math.isfinite(v) for v in vector) or not any(vector):
                            raise ValueError("Embedding vector must be finite, nonzero and 1536-dimensional")
                        report["results"]["embeddings"] = {
                            "model": embedding.model,
                            "dimensions": len(vector),
                            "usage": embedding.usage.model_dump(mode="json"),
                        }
            report["status"] = "passed"
        except (APIError, AzureError, ValueError) as exc:
            report["status"] = "failed"
            report["error"] = {
                "type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
            }
            raise
        finally:
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            receipt.seek(0)
            json.dump(report, receipt, indent=2)
            receipt.truncate()
            receipt.flush()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
