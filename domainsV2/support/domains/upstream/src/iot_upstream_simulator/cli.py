"""Command line interface for the fictional upstream production simulator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .adls import (
    DASHBOARD_PUBLIC_RUNS_PREFIX,
    DEFAULT_PATH_PREFIX,
    DEFAULT_RUN_PATH_PREFIX,
    AdlsError,
    AdlsMonitoringLogSink,
    AdlsRunPublicationSink,
    AdlsSink,
)
from .config import ConfigError, load_config
from .simulator import generate_dataset


def _default_auth_mode() -> str:
    return "managed-identity" if os.getenv("WEBSITE_INSTANCE_ID") else "default"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="iot-upstream-simulator")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="generate fictional upstream production artifacts")
    generate.add_argument("--config", required=True, type=Path)
    generate.add_argument("--output", required=True, type=Path)
    generate.add_argument("--force", action="store_true")
    generate.add_argument(
        "--log-adls-account-url",
        default=os.getenv("IOT_UPSTREAM_LOG_ADLS_ACCOUNT_URL"),
    )
    generate.add_argument(
        "--log-adls-file-system",
        default=os.getenv("IOT_UPSTREAM_LOG_ADLS_FILE_SYSTEM"),
    )
    generate.add_argument(
        "--log-adls-path-prefix",
        default=os.getenv("IOT_UPSTREAM_LOG_ADLS_PATH_PREFIX", DEFAULT_PATH_PREFIX),
    )
    generate.add_argument(
        "--run-adls-account-url",
        default=os.getenv("IOT_UPSTREAM_RUN_ADLS_ACCOUNT_URL"),
        help="publish the full run, including the approved inventory families",
    )
    generate.add_argument(
        "--run-adls-file-system",
        default=os.getenv("IOT_UPSTREAM_RUN_ADLS_FILE_SYSTEM"),
    )
    generate.add_argument(
        "--run-adls-path-prefix",
        default=os.getenv("IOT_UPSTREAM_RUN_ADLS_PATH_PREFIX", DEFAULT_RUN_PATH_PREFIX),
    )
    generate.add_argument(
        "--adls-mode",
        choices=("stream", "upload"),
        default=os.getenv("IOT_UPSTREAM_ADLS_MODE", "upload"),
    )
    generate.add_argument(
        "--azure-auth",
        choices=("default", "managed-identity"),
        default=os.getenv("IOT_UPSTREAM_AZURE_AUTH", _default_auth_mode()),
    )
    generate.add_argument("--managed-identity-client-id", default=os.getenv("AZURE_CLIENT_ID"))
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
            "Azure-hosted monitoring ADLS output requires --azure-auth managed-identity"
        )
    sink = AdlsSink(
        account_url=args.log_adls_account_url,
        file_system=args.log_adls_file_system,
        path_prefix=args.log_adls_path_prefix,
        mode=args.adls_mode,
        auth_mode=args.azure_auth,
        managed_identity_client_id=(
            args.managed_identity_client_id if args.azure_auth == "managed-identity" else None
        ),
        overwrite=args.adls_overwrite,
    )
    return AdlsMonitoringLogSink(sink)


def _is_same_or_inside(candidate: str, namespace: str) -> bool:
    """Whether ``candidate`` is ``namespace`` or a directory beneath it.

    Comparing the raw strings is not enough. A trailing slash makes two names
    for one ADLS directory compare unequal, and an exact-match check lets a
    child path slip past even though readers of the parent enumerate
    recursively. Both are normalised here so a prefix guard cannot be defeated
    by spelling.
    """
    candidate = candidate.strip("/")
    namespace = namespace.strip("/")
    return candidate == namespace or candidate.startswith(f"{namespace}/")


def _build_run_sink(args: argparse.Namespace) -> AdlsRunPublicationSink | None:
    if bool(args.run_adls_account_url) != bool(args.run_adls_file_system):
        raise AdlsError(
            "run ADLS publication requires both --run-adls-account-url and "
            "--run-adls-file-system"
        )
    if not args.run_adls_account_url:
        return None
    if os.getenv("WEBSITE_INSTANCE_ID") and args.azure_auth != "managed-identity":
        raise AdlsError(
            "Azure-hosted run ADLS publication requires --azure-auth managed-identity"
        )
    run_prefix = args.run_adls_path_prefix.strip("/")
    log_prefix = args.log_adls_path_prefix.strip("/")
    if _is_same_or_inside(run_prefix, log_prefix):
        raise AdlsError(
            "run and monitoring ADLS prefixes must differ, and the run prefix "
            "must not sit inside the monitoring prefix, or published run "
            "artifacts would land in the monitoring log stream"
        )
    if _is_same_or_inside(run_prefix, DASHBOARD_PUBLIC_RUNS_PREFIX):
        raise AdlsError(
            "run ADLS prefix must not be, or sit inside, the dashboard's "
            f"public run namespace ({DASHBOARD_PUBLIC_RUNS_PREFIX}); the "
            "dashboard enumerates that path recursively and publishes workflow "
            "records only, so full runs carrying inventory families must not "
            "be written anywhere beneath it"
        )
    sink = AdlsSink(
        account_url=args.run_adls_account_url,
        file_system=args.run_adls_file_system,
        path_prefix=args.run_adls_path_prefix,
        mode=args.adls_mode,
        auth_mode=args.azure_auth,
        managed_identity_client_id=(
            args.managed_identity_client_id if args.azure_auth == "managed-identity" else None
        ),
        overwrite=args.adls_overwrite,
    )
    return AdlsRunPublicationSink(sink)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = generate_dataset(
            load_config(args.config),
            args.output,
            force=args.force,
            log_sink=_build_log_sink(args),
            public_sink=_build_run_sink(args),
        )
    except (AdlsError, ConfigError, FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"generated {manifest['run_id']} in {args.output}")
    return 0


__all__ = ["main"]
