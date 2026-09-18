"""Assemble an explicitly approved, source-only bootstrap ZIP without secret files."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from .bootstrap import canonical, load_manifest
from .package import ROOT, SEARCH_SCHEMA_ASSET, non_secret_json, sources, validate_function_zip, write_zip


def bundle(root: Path, manifest_path: Path, package_directory: Path, output: Path) -> dict:
    raw = manifest_path.read_bytes()
    document = json.loads(raw)
    non_secret_json(document)
    environment = {
        "ITOPS_BOOTSTRAP_AUTHORIZED": "true",
        "ITOPS_BOOTSTRAP_MANIFEST_SHA256": sha256(raw).hexdigest(),
        **{env: document.get(key) for key, env in {
            "bootstrap_client_id": "AZURE_CLIENT_ID", "bootstrap_site": "WEBSITE_SITE_NAME",
            "sql_server": "ITOPS_SQL_SERVER", "sql_database": "ITOPS_SQL_DATABASE",
            "vault_url": "ITOPS_KEY_VAULT_URL", "search_endpoint": "ITOPS_SEARCH_ENDPOINT",
        }.items()},
    }
    manifest = load_manifest(manifest_path, environment)
    if "search" in manifest["operations"]:
        source_schema = json.loads((root / SEARCH_SCHEMA_ASSET).read_bytes())
        if canonical(manifest["search_index"]) != canonical(source_schema):
            raise ValueError("approved Search definition differs from the publisher's source schema")
    files = sources(root, "bootstrap")
    files["bootstrap-manifest.json"] = raw
    if "grants" in manifest["operations"] and "deployment/sql-grants.json" not in files:
        raise ValueError("cannot package grant execution until exact SQL permission allowlists exist")
    for package in manifest["packages"]:
        content = (package_directory / Path(package["file"]).name).read_bytes()
        if sha256(content).hexdigest() != package["sha256"]:
            raise ValueError("Function package digest does not match approved bootstrap manifest")
        validate_function_zip(content, package["service"])
        files[package["file"]] = content
    result = write_zip(output, files)
    return {**result, "manifest_sha256": environment["ITOPS_BOOTSTRAP_MANIFEST_SHA256"],
            "deployment_id": manifest["deployment_id"], "operations": manifest["operations"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bundle(ROOT, args.manifest, args.package_directory, args.output), sort_keys=True))


if __name__ == "__main__":
    main()
