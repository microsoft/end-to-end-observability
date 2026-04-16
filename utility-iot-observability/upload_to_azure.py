"""
Upload synthetic utility telemetry to Azure IoT Hub
Can also route directly to Event Hub or Cosmos DB

Usage:
  python upload_to_azure.py \
    --mode iothub \
    --connection-string "HostName=...;SharedAccessKeyName=owner;SharedAccessKey=..." \
    --data-file utility_telemetry_20260415_214157.jsonl \
    --batch-size 100
"""

import json
import argparse
import sys
from datetime import datetime
from typing import List, Dict, Any
import asyncio
from collections import defaultdict

def upload_to_iothub(
    connection_string: str,
    data_file: str,
    batch_size: int = 100,
    dry_run: bool = False
):
    """
    Upload telemetry to Azure IoT Hub
    
    connection_string format:
      HostName=<hub>.azure-devices.net;SharedAccessKeyName=owner;SharedAccessKey=<key>
    """
    
    try:
        from azure.iot.hub import IoTHubRegistryManager
        from azure.iot.hub.models import Message
    except ImportError:
        print("ERROR: azure-iot-hub package required")
        print("  pip install azure-iot-hub")
        return False
    
    print(f"\n[INFO] Connecting to IoT Hub...")
    registry_manager = IoTHubRegistryManager.from_connection_string(connection_string)
    
    # Verify connection
    try:
        hub_info = registry_manager.get_service_statistics()
        print(f"[OK] Connected to: {hub_info.properties}")
    except Exception as e:
        print(f"[ERROR] Failed to connect: {e}")
        return False
    
    batch = []
    total_sent = 0
    device_counts = defaultdict(int)
    
    print(f"\n[INFO] Reading {data_file}...")
    
    try:
        with open(data_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[WARN] Line {line_num}: JSON parse error: {e}")
                    continue
                
                device_id = payload.get("device_id")
                if not device_id:
                    print(f"[WARN] Line {line_num}: No device_id in payload")
                    continue
                
                device_counts[device_id] += 1
                batch.append(payload)
                
                # Send batch when full
                if len(batch) >= batch_size:
                    if not dry_run:
                        sent = send_batch_to_iothub(registry_manager, batch)
                        total_sent += sent
                    else:
                        print(f"[DRY RUN] Would send {len(batch)} messages")
                        total_sent += len(batch)
                    batch = []
                    print(f"[OK] Sent {total_sent} messages so far...")
        
        # Send remaining batch
        if batch:
            if not dry_run:
                sent = send_batch_to_iothub(registry_manager, batch)
                total_sent += sent
            else:
                print(f"[DRY RUN] Would send {len(batch)} messages")
                total_sent += len(batch)
    
    except Exception as e:
        print(f"[ERROR] Failed to read file: {e}")
        return False
    
    print(f"\n[SUCCESS] Upload complete!")
    print(f"   Total messages sent: {total_sent}")
    print(f"   Unique devices: {len(device_counts)}")
    print(f"   Message breakdown:")
    for device_id, count in sorted(device_counts.items()):
        print(f"     {device_id}: {count}")
    
    return True

def send_batch_to_iothub(registry_manager, batch: List[Dict[str, Any]]) -> int:
    """Send a batch of messages to IoT Hub"""
    from azure.iot.hub.models import Message
    
    sent = 0
    for payload in batch:
        try:
            device_id = payload["device_id"]
            msg = Message(json.dumps(payload))
            
            # Add custom properties for routing
            msg.properties.add("device_type", payload.get("device_type", "unknown"))
            msg.properties.add("device_id", device_id)
            msg.properties.add("timestamp", payload.get("timestamp", ""))
            
            # Send D2C message
            registry_manager.send_d2c_message(device_id, msg)
            sent += 1
        
        except Exception as e:
            print(f"[WARN] Failed to send message for {device_id}: {e}")
    
    return sent

def upload_to_cosmosdb(
    connection_string: str,
    data_file: str,
    container: str = "telemetry",
    batch_size: int = 100,
    dry_run: bool = False
):
    """Upload telemetry to Cosmos DB (for historical archival/metadata)"""
    
    try:
        from azure.cosmos import CosmosClient, exceptions
    except ImportError:
        print("ERROR: azure-cosmos package required")
        print("  pip install azure-cosmos")
        return False
    
    print(f"\n[INFO] Connecting to Cosmos DB...")
    client = CosmosClient.from_connection_string(connection_string)
    
    # Assume database 'observability' exists
    try:
        database = client.get_database_client("observability")
        container_client = database.get_container_client(container)
        print(f"[OK] Connected to {database.id}/{container}")
    except exceptions.CosmosResourceNotFoundError:
        print(f"[ERROR] Database or container not found")
        return False
    
    batch = []
    total_uploaded = 0
    
    print(f"\n[INFO] Reading {data_file}...")
    
    try:
        with open(data_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Add Cosmos ID and partition key
                payload["id"] = f"{payload.get('device_id')}_{payload.get('timestamp')}"
                payload["partition_key"] = payload.get("device_type", "unknown")
                
                batch.append(payload)
                
                # Batch insert
                if len(batch) >= batch_size:
                    if not dry_run:
                        uploaded = batch_insert_cosmos(container_client, batch)
                        total_uploaded += uploaded
                    else:
                        print(f"[DRY RUN] Would insert {len(batch)} documents")
                        total_uploaded += len(batch)
                    batch = []
                    print(f"[OK] Uploaded {total_uploaded} documents...")
        
        # Upload remaining batch
        if batch:
            if not dry_run:
                uploaded = batch_insert_cosmos(container_client, batch)
                total_uploaded += uploaded
            else:
                print(f"[DRY RUN] Would insert {len(batch)} documents")
                total_uploaded += len(batch)
    
    except Exception as e:
        print(f"[ERROR] Upload failed: {e}")
        return False
    
    print(f"\n[SUCCESS] Cosmos DB upload complete! {total_uploaded} documents")
    return True

def batch_insert_cosmos(container_client, batch: List[Dict]) -> int:
    """Batch insert documents into Cosmos DB"""
    inserted = 0
    for doc in batch:
        try:
            container_client.create_item(body=doc)
            inserted += 1
        except Exception as e:
            print(f"[WARN] Failed to insert document: {e}")
    
    return inserted

def main():
    parser = argparse.ArgumentParser(
        description="Upload synthetic utility telemetry to Azure"
    )
    parser.add_argument(
        "--mode",
        choices=["iothub", "eventhub", "cosmosdb", "dry-run"],
        default="iothub",
        help="Upload destination"
    )
    parser.add_argument(
        "--connection-string",
        required=True,
        help="Azure service connection string"
    )
    parser.add_argument(
        "--data-file",
        required=True,
        help="Path to JSONL data file"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for uploads"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate upload without sending"
    )
    
    args = parser.parse_args()
    
    print(f"\n{'='*70}")
    print(f"Utility Telemetry Azure Uploader")
    print(f"{'='*70}")
    print(f"Mode: {args.mode}")
    print(f"Data file: {args.data_file}")
    print(f"Batch size: {args.batch_size}")
    print(f"Dry run: {args.dry_run}")
    print(f"{'='*70}\n")
    
    if args.mode == "iothub":
        success = upload_to_iothub(
            args.connection_string,
            args.data_file,
            args.batch_size,
            args.dry_run
        )
    elif args.mode == "cosmosdb":
        success = upload_to_cosmosdb(
            args.connection_string,
            args.data_file,
            "telemetry",
            args.batch_size,
            args.dry_run
        )
    else:
        print(f"[ERROR] Mode '{args.mode}' not yet implemented")
        success = False
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
