"""Command-line interface for deterministic dataset generation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

from .config import ConfigError, load_config
from .adls import AdlsError, AdlsSink
from .continuous import (
    ContinuousError,
    build_adls_sink_factory,
    enqueue_clear_incident,
    enqueue_clear_storm,
    enqueue_incident,
    enqueue_stop,
    enqueue_storm,
    run_continuous,
    runtime_status,
)
from .monitor import (
    AzureLogsIngestionPublisher,
    AzureStorageQueueCommandSource,
    MonitorError,
)
from .simulator import generate_dataset
from .inventory import InventoryError, generate_inventory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iot-grid-simulator",
        description="Generate deterministic synthetic US utility IoT data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser(
        "generate-inventory", help="generate a standalone local-only fictional parts inventory"
    )
    inventory.add_argument("--output", required=True, type=Path, help="new or empty output directory")
    inventory.add_argument("--seed", type=int, default=42, help="deterministic nonnegative seed; default 42")
    generate = subparsers.add_parser("generate", help="generate JSONL, CSV, and manifest artifacts")
    generate.add_argument("--config", required=True, type=Path, help="JSON configuration path")
    generate.add_argument("--output", required=True, type=Path, help="output directory")
    generate.add_argument(
        "--force",
        action="store_true",
        help="overwrite simulator-owned files in a non-empty output directory",
    )
    generate.add_argument(
        "--adls-mode",
        choices=("stream", "upload"),
        default=os.getenv("IOT_GRID_ADLS_MODE"),
        help="send artifacts to ADLS Gen2 incrementally or after finalization",
    )
    generate.add_argument(
        "--adls-account-url",
        default=os.getenv("IOT_GRID_ADLS_ACCOUNT_URL"),
        help="ADLS Gen2 DFS endpoint, for example https://account.dfs.core.windows.net",
    )
    generate.add_argument(
        "--adls-file-system",
        default=os.getenv("IOT_GRID_ADLS_FILE_SYSTEM"),
        help="ADLS Gen2 file system/container",
    )
    generate.add_argument(
        "--adls-path-prefix",
        default=os.getenv("IOT_GRID_ADLS_PATH_PREFIX", "iot-grid-simulator"),
        help="remote directory prefix",
    )
    generate.add_argument(
        "--azure-auth",
        choices=("default", "managed-identity"),
        default=os.getenv("IOT_GRID_AZURE_AUTH", "default"),
        help="default for local development; managed-identity for Azure hosting",
    )
    generate.add_argument(
        "--managed-identity-client-id",
        default=os.getenv("AZURE_CLIENT_ID"),
        help="optional user-assigned managed identity client ID",
    )
    generate.add_argument(
        "--adls-overwrite",
        action="store_true",
        help="replace an existing remote run with the same deterministic run ID",
    )
    continuous = subparsers.add_parser(
        "run-continuous",
        help="publish deterministic micro-batches until stopped",
    )
    continuous.add_argument("--config", required=True, type=Path)
    continuous.add_argument("--output", required=True, type=Path)
    continuous.add_argument("--runtime-dir", required=True, type=Path)
    continuous.add_argument(
        "--wall-clock-seconds",
        type=float,
        default=60.0,
        help="real seconds between completed batches; default 60",
    )
    continuous.add_argument(
        "--simulation-interval-minutes",
        type=int,
        default=15,
        help="simulated minutes emitted per batch; fixed after initialization",
    )
    continuous.add_argument(
        "--start-utc",
        help="initial simulated UTC time; defaults to current rounded UTC",
    )
    continuous.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="bounded batch count; zero runs until stopped",
    )
    continuous.add_argument(
        "--recover",
        action="store_true",
        help="remove a stale runtime lock after confirming no runner is active",
    )
    continuous.add_argument(
        "--adls-account-url",
        default=os.getenv("IOT_GRID_ADLS_ACCOUNT_URL"),
    )
    continuous.add_argument(
        "--adls-file-system",
        default=os.getenv("IOT_GRID_ADLS_FILE_SYSTEM"),
    )
    continuous.add_argument(
        "--adls-path-prefix",
        default=os.getenv(
            "IOT_GRID_ADLS_PATH_PREFIX",
            "utility/iot/continuous",
        ),
    )
    continuous.add_argument(
        "--azure-auth",
        choices=("default", "managed-identity"),
        default=os.getenv("IOT_GRID_AZURE_AUTH", "default"),
    )
    continuous.add_argument(
        "--managed-identity-client-id",
        default=os.getenv("AZURE_CLIENT_ID"),
    )
    continuous.add_argument(
        "--command-queue-account-url",
        default=os.getenv("IOT_GRID_COMMAND_QUEUE_ACCOUNT_URL"),
        help="Azure Storage Queue account endpoint for storm commands",
    )
    continuous.add_argument(
        "--command-queue-name",
        default=os.getenv("IOT_GRID_COMMAND_QUEUE_NAME"),
        help="Azure Storage Queue name for storm commands",
    )
    continuous.add_argument(
        "--command-queue-visibility-timeout",
        type=int,
        default=os.getenv("IOT_GRID_COMMAND_QUEUE_VISIBILITY_TIMEOUT", "60"),
        help="queue message visibility timeout in seconds; default 60",
    )
    continuous.add_argument(
        "--monitor-dcr-endpoint",
        default=os.getenv("IOT_GRID_MONITOR_DCR_ENDPOINT"),
        help="Azure Monitor Logs Ingestion DCR endpoint",
    )
    continuous.add_argument(
        "--monitor-dcr-immutable-rule-id",
        default=os.getenv("IOT_GRID_MONITOR_DCR_IMMUTABLE_RULE_ID"),
        help="Azure Monitor immutable DCR rule ID",
    )
    continuous.add_argument(
        "--monitor-stream-name",
        default=os.getenv("IOT_GRID_MONITOR_STREAM_NAME"),
        help="Azure Monitor DCR stream name",
    )
    continuous.add_argument(
        "--monitor-target-resource-id",
        default=os.getenv("IOT_GRID_MONITOR_TARGET_RESOURCE_ID"),
        help="Azure resource ID attached to emitted storm events",
    )

    stop = subparsers.add_parser(
        "stop-continuous",
        help="request a graceful stop at the next tick boundary",
    )
    stop.add_argument("--runtime-dir", required=True, type=Path)

    inject = subparsers.add_parser(
        "inject-storm",
        help="enqueue a synthetic storm-route update",
    )
    inject.add_argument("--runtime-dir", required=True, type=Path)
    inject.add_argument("--storm-config", required=True, type=Path)

    clear = subparsers.add_parser(
        "clear-storm",
        help="clear the active synthetic storm at the next tick boundary",
    )
    clear.add_argument("--runtime-dir", required=True, type=Path)

    inject_incident = subparsers.add_parser(
        "inject-incident",
        help="enqueue a synthetic operational incident",
    )
    inject_incident.add_argument("--runtime-dir", required=True, type=Path)
    inject_incident.add_argument(
        "--incident-config",
        required=True,
        type=Path,
    )

    clear_incident = subparsers.add_parser(
        "clear-incident",
        help="clear the active synthetic incident at the next tick boundary",
    )
    clear_incident.add_argument("--runtime-dir", required=True, type=Path)

    status = subparsers.add_parser(
        "continuous-status",
        help="show checkpoint and command-queue state",
    )
    status.add_argument("--runtime-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate-inventory":
            manifest = generate_inventory(args.output, seed=args.seed)
            print(f"generated {manifest['run_id']} in {args.output}")
            return 0
        if args.command == "stop-continuous":
            command_id = enqueue_stop(args.runtime_dir)
            print(f"stop requested: {command_id}")
            return 0
        if args.command == "inject-storm":
            command_id = enqueue_storm(
                args.runtime_dir,
                args.storm_config,
            )
            print(f"storm update queued: {command_id}")
            return 0
        if args.command == "clear-storm":
            command_id = enqueue_clear_storm(args.runtime_dir)
            print(f"storm clear queued: {command_id}")
            return 0
        if args.command == "inject-incident":
            command_id = enqueue_incident(
                args.runtime_dir,
                args.incident_config,
            )
            print(f"incident queued: {command_id}")
            return 0
        if args.command == "clear-incident":
            command_id = enqueue_clear_incident(args.runtime_dir)
            print(f"incident clear queued: {command_id}")
            return 0
        if args.command == "continuous-status":
            print(json.dumps(runtime_status(args.runtime_dir), indent=2))
            return 0
        if args.command == "run-continuous":
            if bool(args.adls_account_url) != bool(args.adls_file_system):
                raise ContinuousError(
                    "continuous ADLS output requires both account URL "
                    "and file system"
                )
            sink_factory = (
                build_adls_sink_factory(
                    account_url=args.adls_account_url,
                    file_system=args.adls_file_system,
                    path_prefix=args.adls_path_prefix,
                    auth_mode=args.azure_auth,
                    managed_identity_client_id=(
                        args.managed_identity_client_id
                        if args.azure_auth == "managed-identity"
                        else None
                    ),
                )
                if args.adls_account_url
                else None
            )
            queue_values = (
                args.command_queue_account_url,
                args.command_queue_name,
            )
            if any(queue_values) and not all(queue_values):
                raise ContinuousError(
                    "continuous queue commands require both account URL "
                    "and queue name"
                )
            command_source = (
                AzureStorageQueueCommandSource(
                    account_url=args.command_queue_account_url,
                    queue_name=args.command_queue_name,
                    auth_mode=args.azure_auth,
                    managed_identity_client_id=(
                        args.managed_identity_client_id
                        if args.azure_auth == "managed-identity"
                        else None
                    ),
                    visibility_timeout=args.command_queue_visibility_timeout,
                )
                if args.command_queue_account_url
                else None
            )
            monitor_values = (
                args.monitor_dcr_endpoint,
                args.monitor_dcr_immutable_rule_id,
                args.monitor_stream_name,
                args.monitor_target_resource_id,
            )
            if any(monitor_values) and not all(monitor_values):
                raise ContinuousError(
                    "continuous monitor output requires DCR endpoint, "
                    "immutable rule ID, stream name, and target resource ID"
                )
            monitor_publisher = (
                AzureLogsIngestionPublisher(
                    endpoint=args.monitor_dcr_endpoint,
                    rule_id=args.monitor_dcr_immutable_rule_id,
                    stream_name=args.monitor_stream_name,
                    auth_mode=args.azure_auth,
                    managed_identity_client_id=(
                        args.managed_identity_client_id
                        if args.azure_auth == "managed-identity"
                        else None
                    ),
                )
                if args.monitor_dcr_endpoint
                else None
            )
            published = run_continuous(
                config_path=args.config,
                output_root=args.output,
                runtime_directory=args.runtime_dir,
                wall_clock_seconds=args.wall_clock_seconds,
                simulation_interval_minutes=args.simulation_interval_minutes,
                start_utc=args.start_utc,
                max_batches=args.max_batches,
                recover=args.recover,
                sink_factory=sink_factory,
                command_source=command_source,
                monitor_publisher=monitor_publisher,
                monitor_target_resource_id=args.monitor_target_resource_id,
                on_batch=lambda batch: print(
                    "published "
                    f"batch={batch['batch_sequence']:09d} "
                    f"run={batch['run_id']} "
                    f"simulated={batch['simulated_start_utc']}",
                    flush=True,
                ),
            )
            print(f"continuous runner stopped after {len(published)} batch(es)")
            return 0

        config = load_config(args.config)
        sink = None
        if args.adls_mode is not None:
            if not args.adls_account_url or not args.adls_file_system:
                raise AdlsError(
                    "ADLS mode requires --adls-account-url and --adls-file-system"
                )
            sink = AdlsSink(
                account_url=args.adls_account_url,
                file_system=args.adls_file_system,
                path_prefix=args.adls_path_prefix,
                mode=args.adls_mode,
                auth_mode=args.azure_auth,
                managed_identity_client_id=(
                    args.managed_identity_client_id
                    if args.azure_auth == "managed-identity"
                    else None
                ),
                overwrite=args.adls_overwrite,
            )
        manifest = generate_dataset(
            config,
            args.output,
            force=args.force,
            sink=sink,
        )
    except (
        AdlsError,
        ConfigError,
        ContinuousError,
        MonitorError,
        InventoryError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"generated {manifest['run_id']} in {args.output}")
    if sink is not None:
        print(
            "uploaded "
            f"{sink.account_url}/{sink.file_system_name}/"
            f"{sink.remote_run_path(manifest['run_id'])}"
        )
    return 0
