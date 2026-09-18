"""Command line interface for the fictional downstream refinery simulator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .adls import AdlsError, AdlsMonitoringLogSink, AdlsSink
from .config import ConfigError, load_config
from .simulator import generate_dataset


def _default_auth_mode() -> str:
    return "managed-identity" if os.getenv("WEBSITE_INSTANCE_ID") else "default"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iot-downstream-refinery-simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser(
        "generate",
        help="generate fictional downstream refinery artifacts",
    )
    generate.add_argument("--config", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--force", action="store_true")
    generate.add_argument(
        "--log-adls-account-url",
        default=os.getenv("IOT_DOWNSTREAM_LOG_ADLS_ACCOUNT_URL"),
    )
    generate.add_argument(
        "--log-adls-file-system",
        default=os.getenv("IOT_DOWNSTREAM_LOG_ADLS_FILE_SYSTEM"),
    )
    generate.add_argument(
        "--log-adls-path-prefix",
        default=os.getenv(
            "IOT_DOWNSTREAM_LOG_ADLS_PATH_PREFIX",
            "downstream-refinery/logs",
        ),
    )
    generate.add_argument(
        "--adls-mode",
        choices=("stream", "upload"),
        default=os.getenv("IOT_DOWNSTREAM_ADLS_MODE", "upload"),
    )
    generate.add_argument(
        "--azure-auth",
        choices=("default", "managed-identity"),
        default=os.getenv("IOT_DOWNSTREAM_AZURE_AUTH", _default_auth_mode()),
    )
    generate.add_argument(
        "--managed-identity-client-id",
        default=os.getenv("AZURE_CLIENT_ID"),
    )
    generate.add_argument("--adls-overwrite", action="store_true")
    return parser


def _build_log_sink(args: argparse.Namespace) -> AdlsMonitoringLogSink | None:
    if bool(args.log_adls_account_url) != bool(args.log_adls_file_system):
        raise AdlsError(
            "monitoring ADLS output requires both --log-adls-account-url and "
            "--log-adls-file-system"
        )
    if not args.log_adls_account_url:
        return None
    if os.getenv("WEBSITE_INSTANCE_ID") and args.azure_auth != "managed-identity":
        raise AdlsError(
            "Azure-hosted monitoring ADLS output requires "
            "--azure-auth managed-identity"
        )
    sink = AdlsSink(
        account_url=args.log_adls_account_url,
        file_system=args.log_adls_file_system,
        path_prefix=args.log_adls_path_prefix,
        mode=args.adls_mode,
        auth_mode=args.azure_auth,
        managed_identity_client_id=(
            args.managed_identity_client_id
            if args.azure_auth == "managed-identity"
            else None
        ),
        overwrite=args.adls_overwrite,
    )
    return AdlsMonitoringLogSink(sink)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = generate_dataset(
            load_config(args.config),
            args.output,
            force=args.force,
            log_sink=_build_log_sink(args),
        )
    except (AdlsError, ConfigError, FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"generated {manifest['run_id']} in {args.output}")
    return 0
