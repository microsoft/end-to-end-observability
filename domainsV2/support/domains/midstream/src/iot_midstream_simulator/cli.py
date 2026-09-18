"""Command line for the fictional liquid-midstream simulator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .adls import AdlsError, AdlsSink, AdlsSyntheticLogSink
from .config import ConfigError, load_config
from .simulator import generate_dataset


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iot-midstream-simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="generate fictional midstream IoT artifacts")
    generate.add_argument("--config", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--adls-account-url", default=os.getenv("IOT_MIDSTREAM_ADLS_ACCOUNT_URL"))
    generate.add_argument("--adls-file-system", default=os.getenv("IOT_MIDSTREAM_ADLS_FILE_SYSTEM"))
    generate.add_argument("--adls-path-prefix", default=os.getenv("IOT_MIDSTREAM_ADLS_PATH_PREFIX", "midstream/iot"))
    generate.add_argument("--log-adls-file-system", default=os.getenv("IOT_MIDSTREAM_LOG_ADLS_FILE_SYSTEM"))
    generate.add_argument("--log-adls-path-prefix", default=os.getenv("IOT_MIDSTREAM_LOG_ADLS_PATH_PREFIX", "midstream/logs"))
    generate.add_argument("--adls-mode", choices=("stream", "upload"), default=os.getenv("IOT_MIDSTREAM_ADLS_MODE", "upload"))
    generate.add_argument("--azure-auth", choices=("default", "managed-identity"), default=os.getenv("IOT_MIDSTREAM_AZURE_AUTH", "default"))
    generate.add_argument("--managed-identity-client-id", default=os.getenv("AZURE_CLIENT_ID"))
    generate.add_argument("--adls-overwrite", action="store_true")
    return parser


def _sink(args: argparse.Namespace, file_system: str, path_prefix: str) -> AdlsSink:
    return AdlsSink(
        account_url=args.adls_account_url,
        file_system=file_system,
        path_prefix=path_prefix,
        mode=args.adls_mode,
        auth_mode=args.azure_auth,
        managed_identity_client_id=args.managed_identity_client_id if args.azure_auth == "managed-identity" else None,
        overwrite=args.adls_overwrite,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if bool(args.adls_account_url) != bool(args.adls_file_system):
            raise AdlsError("primary ADLS output requires account URL and file system")
        if args.log_adls_file_system and not args.adls_account_url:
            raise AdlsError("log ADLS output requires --adls-account-url")
        primary = _sink(args, args.adls_file_system, args.adls_path_prefix) if args.adls_account_url else None
        logs = (
            AdlsSyntheticLogSink(_sink(args, args.log_adls_file_system, args.log_adls_path_prefix))
            if args.log_adls_file_system
            else None
        )
        manifest = generate_dataset(
            load_config(args.config),
            args.output,
            force=args.force,
            sink=primary,
            log_sink=logs,
        )
    except (AdlsError, ConfigError, FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"generated {manifest['run_id']} in {args.output}")
    return 0
