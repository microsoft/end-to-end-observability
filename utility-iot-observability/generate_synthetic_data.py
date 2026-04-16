"""
Utility IoT Synthetic Data Generator
Produces realistic fake telemetry for electric, gas, and oil/gas utilities in NY State
Data ready for Azure IoT Hub / Event Hub ingestion

Generated: 2026-04-15
"""

import json
import csv
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import math

# NY State utility geography
LOCATIONS = {
    "substations": [
        {"id": "SS_ALB_001", "name": "Albany Colonie", "lat": 42.7284, "lon": -73.7893},
        {"id": "SS_POK_001", "name": "Poughkeepsie Main", "lat": 41.7004, "lon": -73.9209},
        {"id": "SS_KIN_001", "name": "Kingston", "lat": 41.9270, "lon": -74.0234},
        {"id": "SS_BIN_001", "name": "Binghamton Central", "lat": 42.0987, "lon": -75.9180},
        {"id": "SS_NEW_001", "name": "Newburgh", "lat": 41.5034, "lon": -74.0104},
    ],
    "transformers": [
        {"id": "TX_ALB_001", "substation": "SS_ALB_001", "lat": 42.7290, "lon": -73.7900, "rating_kva": 75},
        {"id": "TX_ALB_002", "substation": "SS_ALB_001", "lat": 42.7280, "lon": -73.7890, "rating_kva": 100},
        {"id": "TX_POK_001", "substation": "SS_POK_001", "lat": 41.7010, "lon": -73.9210, "rating_kva": 150},
        {"id": "TX_POK_002", "substation": "SS_POK_001", "lat": 41.7000, "lon": -73.9205, "rating_kva": 200},
        {"id": "TX_KIN_001", "substation": "SS_KIN_001", "lat": 41.9275, "lon": -74.0235, "rating_kva": 75},
    ],
    "meters": [
        {"id": "MTR_ALB_001001", "lat": 42.7300, "lon": -73.7900, "type": "residential"},
        {"id": "MTR_ALB_001002", "lat": 42.7305, "lon": -73.7895, "type": "residential"},
        {"id": "MTR_ALB_002001", "lat": 42.7310, "lon": -73.7905, "type": "commercial"},
        {"id": "MTR_POK_001001", "lat": 41.7010, "lon": -73.9215, "type": "residential"},
        {"id": "MTR_POK_001002", "lat": 41.7015, "lon": -73.9210, "type": "residential"},
        {"id": "MTR_POK_003001", "lat": 41.7020, "lon": -73.9205, "type": "industrial"},
    ],
    "gas_meters": [
        {"id": "GMR_ALB_001001", "lat": 42.7300, "lon": -73.7900, "type": "residential"},
        {"id": "GMR_ALB_001002", "lat": 42.7305, "lon": -73.7895, "type": "residential"},
        {"id": "GMR_POK_001001", "lat": 41.7010, "lon": -73.9215, "type": "residential"},
    ],
    "regulators": [
        {"id": "REG_ALB_001", "lat": 42.7250, "lon": -73.7850, "inlet_psi": 300},
        {"id": "REG_POK_001", "lat": 41.6950, "lon": -73.9150, "inlet_psi": 280},
        {"id": "REG_KIN_001", "lat": 41.9220, "lon": -74.0200, "inlet_psi": 320},
    ],
    "cp_stations": [
        {"id": "CP_GAS_001", "lat": 42.7100, "lon": -73.8000},
        {"id": "CP_GAS_002", "lat": 41.7050, "lon": -73.9300},
        {"id": "CP_GAS_003", "lat": 41.9200, "lon": -74.0300},
    ],
    "pipeline_stations": [
        {"id": "PIPE_COMP_001", "name": "Middletown Compressor", "lat": 41.4459, "lon": -74.4224, "type": "compressor"},
        {"id": "PIPE_VALVE_001", "name": "Hudson Valve Station", "lat": 41.7900, "lon": -73.9300, "type": "valve"},
        {"id": "PIPE_METER_001", "name": "Selkirk Meter Station", "lat": 42.5384, "lon": -73.8569, "type": "metering"},
    ],
}

def generate_smart_meter_reading(meter_id: str, meter_type: str = "residential", timestamp: datetime = None):
    """Generate realistic electric smart meter reading (15-min interval)"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    # Realistic consumption patterns by meter type
    base_kw = {"residential": 1.2, "commercial": 4.5, "industrial": 25.0}.get(meter_type, 1.0)
    
    # Vary by hour of day (peak demand 17:00-21:00)
    hour = timestamp.hour
    if 6 <= hour < 9:
        multiplier = 1.3  # Morning peak
    elif 17 <= hour < 21:
        multiplier = 1.8  # Evening peak
    else:
        multiplier = 0.7
    
    kw = base_kw * multiplier * random.uniform(0.8, 1.2)
    kwh = kw * 0.25  # 15 minutes = 0.25 hours
    
    return {
        "device_type": "smart_meter",
        "device_id": meter_id,
        "timestamp": timestamp.isoformat() + "Z",
        "kWh_delivered": round(kwh, 4),
        "kWh_received": round(random.uniform(0, 2), 4),  # Solar
        "voltage_a": round(random.uniform(118, 124), 2),
        "voltage_b": round(random.uniform(117, 123), 2),
        "voltage_c": round(random.uniform(118, 124), 2) if random.random() > 0.7 else None,
        "current_a": round(random.uniform(5, 100), 2),
        "power_factor": round(random.uniform(0.92, 1.00), 3),
        "frequency_hz": round(random.uniform(59.95, 60.05), 3),
        "demand_kw": round(kw, 2),
        "tamper_flag": random.random() < 0.001,  # 0.1% chance
        "outage_flag": random.random() < 0.0001,  # 0.01% chance
        "rssi_dbm": random.randint(-110, -40),
    }

def generate_transformer_reading(tx_id: str, timestamp: datetime = None):
    """Generate realistic transformer monitoring data (1-5 min interval)"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    # Realistic loading pattern
    load_pct = random.uniform(20, 85)
    
    # DGA concentrations (healthy transformer baseline with some variance)
    h2 = random.uniform(50, 300)
    ch4 = random.uniform(20, 150)
    c2h2 = random.uniform(0, 5)  # Should stay low
    c2h4 = random.uniform(10, 100)
    c2h6 = random.uniform(5, 80)
    co = random.uniform(100, 800)
    co2 = random.uniform(1000, 5000)
    
    # Fault flag: rare DGA values indicate developing fault
    fault_flag = (h2 > 400 or c2h2 > 20 or ch4 > 200)
    
    return {
        "device_type": "transformer",
        "device_id": tx_id,
        "timestamp": timestamp.isoformat() + "Z",
        "oil_temp_top_c": round(random.uniform(35, 95), 1),
        "oil_temp_bottom_c": round(random.uniform(30, 75), 1),
        "ambient_temp_c": round(random.uniform(0, 35), 1),
        "load_kw": round(load_pct * 0.75, 1),  # Assume 75 kVA nominal
        "load_pct": round(load_pct, 1),
        "voltage_primary_kv": round(random.uniform(13.8 * 0.95, 13.8 * 1.05), 2),
        "voltage_secondary_v": round(random.uniform(118, 124), 1),
        "current_a_phase_a": round(random.uniform(10, 150), 1),
        "current_a_phase_b": round(random.uniform(10, 150), 1),
        "current_a_phase_c": round(random.uniform(10, 150), 1),
        "tap_position": random.randint(1, 33),
        "dga_h2_ppm": round(h2, 1),
        "dga_ch4_ppm": round(ch4, 1),
        "dga_c2h2_ppm": round(c2h2, 2),
        "dga_c2h4_ppm": round(c2h4, 1),
        "dga_c2h6_ppm": round(c2h6, 1),
        "dga_co_ppm": round(co, 1),
        "dga_co2_ppm": round(co2, 1),
        "moisture_ppm": round(random.uniform(5, 45), 1),
        "bushing_cap_pf": round(random.uniform(0.003, 0.012), 4),
        "partial_discharge_pc": round(random.uniform(0, 300), 1),
        "fault_detected": fault_flag,
    }

def generate_gas_meter_reading(meter_id: str, meter_type: str = "residential", timestamp: datetime = None):
    """Generate realistic gas meter reading (hourly)"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    # Realistic consumption patterns by meter type
    base_ccf = {"residential": 0.8, "commercial": 2.5}.get(meter_type, 0.5)
    
    # Vary by season (heating season Oct-Apr)
    month = timestamp.month
    if month in [10, 11, 12, 1, 2, 3]:
        multiplier = 1.5
    else:
        multiplier = 0.3
    
    ccf = base_ccf * multiplier * random.uniform(0.7, 1.3)
    
    return {
        "device_type": "gas_meter",
        "device_id": meter_id,
        "timestamp": timestamp.isoformat() + "Z",
        "volume_ccf": round(ccf, 3),
        "corrected_volume_therms": round(ccf * 0.97, 2),  # Correction factor
        "pressure_psig": round(random.uniform(5, 30), 2),
        "temperature_f": round(random.uniform(35, 95), 1),
        "battery_voltage": round(random.uniform(2.8, 3.5), 2),
        "tamper_flag": random.random() < 0.001,
        "leak_alarm": random.random() < 0.0001,
    }

def generate_regulator_reading(reg_id: str, inlet_psi: float = 300, timestamp: datetime = None):
    """Generate realistic gas pressure regulator reading"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    # Outlet should be lower and more stable
    outlet = random.uniform(8, 25)
    
    return {
        "device_type": "gas_regulator",
        "device_id": reg_id,
        "timestamp": timestamp.isoformat() + "Z",
        "inlet_pressure_psig": round(inlet_psi + random.uniform(-5, 5), 1),
        "outlet_pressure_psig": round(outlet, 2),
        "flow_rate_mcfh": round(random.uniform(100, 2000), 1),
        "gas_temperature_f": round(random.uniform(35, 85), 1),
        "regulator_position_pct": round(random.uniform(20, 90), 1),
        "relief_valve_status": "NORMAL" if random.random() > 0.01 else "OPEN",
        "slam_shut_status": "NORMAL" if random.random() > 0.001 else "TRIPPED",
        "odorizer_level_pct": round(random.uniform(20, 100), 1),
    }

def generate_cathodic_protection_reading(cp_id: str, timestamp: datetime = None):
    """Generate cathodic protection station reading"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    return {
        "device_type": "cathodic_protection",
        "device_id": cp_id,
        "timestamp": timestamp.isoformat() + "Z",
        "pipe_to_soil_mv": round(random.uniform(-1200, -900), 1),  # Should be -850 to -1500
        "rectifier_voltage": round(random.uniform(5, 80), 1),
        "rectifier_current": round(random.uniform(5, 40), 1),
        "ac_supply_voltage": round(random.uniform(115, 125), 1),
        "rectifier_status": "ON" if random.random() > 0.01 else ("OFF" if random.random() > 0.5 else "FAULT"),
    }

def generate_pipeline_reading(station_id: str, station_type: str = "compressor", timestamp: datetime = None):
    """Generate pipeline SCADA reading"""
    if timestamp is None:
        timestamp = datetime.utcnow()
    
    if station_type == "compressor":
        return {
            "device_type": "pipeline_compressor",
            "device_id": station_id,
            "timestamp": timestamp.isoformat() + "Z",
            "pressure_psig": round(random.uniform(800, 1400), 1),
            "temperature_f": round(random.uniform(80, 120), 1),
            "flow_rate_mcfh": round(random.uniform(5000, 15000), 1),
            "pump_status": "RUN" if random.random() > 0.02 else "STOP",
            "pump_discharge_psi": round(random.uniform(900, 1500), 1),
            "pump_suction_psi": round(random.uniform(200, 500), 1),
            "vibration_mms": round(random.uniform(2, 15), 2),
            "h2s_ppm": round(random.uniform(0, 20), 1),
        }
    
    elif station_type == "valve":
        return {
            "device_type": "pipeline_valve",
            "device_id": station_id,
            "timestamp": timestamp.isoformat() + "Z",
            "pressure_psig": round(random.uniform(600, 1200), 1),
            "temperature_f": round(random.uniform(60, 100), 1),
            "valve_position_pct": round(random.uniform(0, 100), 1),
            "flow_rate_mcfh": round(random.uniform(3000, 12000), 1),
            "leak_detection_flag": random.random() < 0.0001,
        }
    
    else:  # metering
        return {
            "device_type": "pipeline_metering",
            "device_id": station_id,
            "timestamp": timestamp.isoformat() + "Z",
            "pressure_psig": round(random.uniform(500, 900), 1),
            "temperature_f": round(random.uniform(50, 90), 1),
            "flow_rate_mcfh": round(random.uniform(2000, 10000), 1),
            "volume_mcf": round(random.uniform(500, 2000), 1),
            "pressure_ratio": round(random.uniform(0.8, 1.0), 3),
        }

def generate_dataset(output_dir: str = ".", num_hours: int = 24, interval_minutes: int = 15):
    """
    Generate a full day's worth of synthetic utility telemetry data
    
    Args:
        output_dir: Directory to write output files
        num_hours: Number of hours to generate
        interval_minutes: Sampling interval
    """
    
    now = datetime.utcnow()
    end_time = now - timedelta(hours=num_hours)
    
    all_records = []
    
    # Generate data
    current_time = end_time
    while current_time <= now:
        # Electric meters (15-min intervals)
        if current_time.minute % 15 == 0:
            for meter in LOCATIONS["meters"]:
                all_records.append(generate_smart_meter_reading(
                    meter["id"], 
                    meter["type"],
                    current_time
                ))
        
        # Transformers (1-5 min, we'll do 5-min for volume)
        if current_time.minute % 5 == 0:
            for tx in LOCATIONS["transformers"]:
                all_records.append(generate_transformer_reading(tx["id"], current_time))
        
        # Gas meters (1 hour)
        if current_time.minute == 0:
            for meter in LOCATIONS["gas_meters"]:
                all_records.append(generate_gas_meter_reading(
                    meter["id"],
                    meter["type"],
                    current_time
                ))
            
            # Regulators (1 hour)
            for reg in LOCATIONS["regulators"]:
                all_records.append(generate_regulator_reading(
                    reg["id"],
                    reg["inlet_psi"],
                    current_time
                ))
            
            # Cathodic protection (1 hour)
            for cp in LOCATIONS["cp_stations"]:
                all_records.append(generate_cathodic_protection_reading(
                    cp["id"],
                    current_time
                ))
            
            # Pipeline stations (1-10 sec in reality, we'll do 1 hour for simulation)
            for station in LOCATIONS["pipeline_stations"]:
                all_records.append(generate_pipeline_reading(
                    station["id"],
                    station["type"],
                    current_time
                ))
        
        current_time += timedelta(minutes=1)
    
    # Write to JSONL (one JSON per line — ideal for Azure Event Hub)
    jsonl_file = f"{output_dir}/utility_telemetry_{now.strftime('%Y%m%d_%H%M%S')}.jsonl"
    with open(jsonl_file, "w") as f:
        for record in all_records:
            f.write(json.dumps(record) + "\n")
    
    # Write to CSV for easy review
    csv_file = f"{output_dir}/utility_telemetry_{now.strftime('%Y%m%d_%H%M%S')}.csv"
    if all_records:
        with open(csv_file, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_records[0].keys(), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_records)
    
    # Write metadata
    metadata = {
        "generated_at": now.isoformat() + "Z",
        "total_records": len(all_records),
        "hours_generated": num_hours,
        "device_counts": {
            "smart_meters": len(LOCATIONS["meters"]),
            "gas_meters": len(LOCATIONS["gas_meters"]),
            "transformers": len(LOCATIONS["transformers"]),
            "regulators": len(LOCATIONS["regulators"]),
            "cathodic_protection": len(LOCATIONS["cp_stations"]),
            "pipeline_stations": len(LOCATIONS["pipeline_stations"]),
        },
        "output_files": {
            "jsonl": jsonl_file,
            "csv": csv_file,
        }
    }
    
    meta_file = f"{output_dir}/metadata_{now.strftime('%Y%m%d_%H%M%S')}.json"
    with open(meta_file, "w") as f:
        json.dump(metadata, f, indent=2)
    
    print(f"\n[OK] Synthetic data generated!")
    print(f"   Total records: {len(all_records)}")
    print(f"   JSONL: {jsonl_file}")
    print(f"   CSV: {csv_file}")
    print(f"   Metadata: {meta_file}")
    
    return all_records, metadata

if __name__ == "__main__":
    import sys
    output_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    records, meta = generate_dataset(output_dir, num_hours=24)
