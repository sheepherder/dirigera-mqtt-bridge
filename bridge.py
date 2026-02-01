#!/usr/bin/env python3
"""
DIRIGERA to MQTT Bridge

Verbindet den IKEA DIRIGERA Hub mit einem MQTT Broker.

Features:
- WebSocket Event Listener für Echtzeit-Updates
- Periodisches Polling als Backup
- Deduplizierung redundanter Updates
- Unterstützt: Sensoren, Lampen, Steckdosen, Luftreiniger, Bewegungsmelder, Tür/Fenster-Sensoren

Projekt: https://github.com/???/dirigera-mqtt-bridge
"""

import dirigera
import paho.mqtt.client as mqtt
import json
import time
import os
import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional

# =============================================================================
# Logging
# =============================================================================
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format='%(asctime)s - %(levelname)s - [%(threadName)s] %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Konfiguration (Umgebungsvariablen)
# =============================================================================
DIRIGERA_TOKEN = os.environ.get("DIRIGERA_TOKEN")
DIRIGERA_IP = os.environ.get("DIRIGERA_IP", "192.168.0.1")
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_BASE_TOPIC = os.environ.get("MQTT_BASE_TOPIC", "dirigera")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "300"))  # Sekunden
DEDUP_WINDOW = int(os.environ.get("DEDUP_WINDOW", "5"))  # Sekunden

# =============================================================================
# Globale Variablen
# =============================================================================
mqtt_client: Optional[mqtt.Client] = None
hub: Optional[dirigera.Hub] = None
last_values: Dict[str, Dict] = {}
last_publish_times: Dict[str, datetime] = {}
last_websocket_update: Dict[str, datetime] = {}
last_poll_time: Optional[datetime] = None
device_cache: Dict[str, Any] = {}


# =============================================================================
# MQTT
# =============================================================================
def create_mqtt_client() -> mqtt.Client:
    """Erstellt und verbindet MQTT Client."""
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="dirigera-bridge",
        protocol=mqtt.MQTTv311
    )

    if MQTT_USER and MQTT_PASSWORD:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logger.info(f"MQTT verbunden mit {MQTT_HOST}:{MQTT_PORT}")
        else:
            logger.error(f"MQTT Verbindungsfehler: {reason_code}")

    def on_disconnect(c, userdata, flags, reason_code, properties):
        logger.warning(f"MQTT getrennt (rc={reason_code}), Reconnect...")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=60)

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    return client


# =============================================================================
# Deduplizierung
# =============================================================================
def is_duplicate(device_id: str, new_data: Dict) -> bool:
    """
    Deduplizierung: Filtert identische Updates innerhalb von DEDUP_WINDOW Sekunden.
    """
    if device_id not in last_publish_times:
        return False

    time_since_last = (datetime.utcnow() - last_publish_times[device_id]).total_seconds()
    if time_since_last >= DEDUP_WINDOW:
        return False

    # Innerhalb Zeitfenster: Duplikat wenn Werte identisch
    old_data = last_values.get(device_id, {})
    new_compare = {k: v for k, v in new_data.items() if k not in ("timestamp", "source")}
    old_compare = {k: v for k, v in old_data.items() if k not in ("timestamp", "source")}

    if new_compare == old_compare:
        logger.debug(f"Duplikat: Skip {device_id}")
        return True
    return False


def should_poll_send(device_id: str, new_data: Dict) -> bool:
    """
    Prüft ob Poll-Daten gesendet werden sollen.
    Skip wenn WebSocket kürzlich aktuellere Daten geliefert hat.
    """
    if last_poll_time is None:
        return True

    # Wertänderung? Immer senden
    if device_id in last_values:
        old_data = last_values[device_id]
        new_compare = {k: v for k, v in new_data.items() if k != "timestamp"}
        old_compare = {k: v for k, v in old_data.items() if k != "timestamp"}
        if new_compare != old_compare:
            return True

    # Kein WebSocket-Update bekannt
    if device_id not in last_websocket_update:
        return True

    # WebSocket war aktueller als letzter Poll
    if last_websocket_update[device_id] > last_poll_time:
        logger.debug(f"Poll-Skip: {device_id} (WebSocket aktueller)")
        return False

    return True


def publish_to_mqtt(topic: str, data: Dict, device_id: str, from_websocket: bool = False):
    """Publiziert Daten nach MQTT mit Logging."""
    if is_duplicate(device_id, data):
        return

    try:
        mqtt_client.publish(topic, json.dumps(data), retain=False, qos=1)
        now = datetime.utcnow()
        last_publish_times[device_id] = now
        last_values[device_id] = data.copy()
        if from_websocket:
            last_websocket_update[device_id] = now

        # Schönes Logging
        name = data.get("device_name", device_id[:8])
        values = format_log_values(data)
        if values:
            logger.info(f"{name}: {values}")

    except Exception as e:
        logger.error(f"MQTT Publish Fehler: {e}")


def format_log_values(data: Dict) -> str:
    """Formatiert Messwerte für Log-Ausgabe."""
    parts = []
    if "temperature" in data:
        parts.append(f"{data['temperature']}°C")
    if "humidity" in data:
        parts.append(f"{data['humidity']}% RH")
    if "co2" in data:
        parts.append(f"{data['co2']} ppm CO₂")
    if "pm25" in data:
        parts.append(f"{data['pm25']} µg/m³ PM2.5")
    if "voc_index" in data:
        parts.append(f"VOC {data['voc_index']}")
    if "is_on" in data:
        parts.append("AN" if data["is_on"] else "AUS")
    if "is_open" in data:
        parts.append("OFFEN" if data["is_open"] else "ZU")
    if "is_detected" in data:
        parts.append("BEWEGUNG" if data["is_detected"] else "ruhig")
    if "brightness" in data:
        parts.append(f"{data['brightness']}%")
    if "power" in data:
        parts.append(f"{data['power']}W")
    if "battery_percentage" in data:
        parts.append(f"Batterie {data['battery_percentage']}%")
    return ", ".join(parts)


# =============================================================================
# Daten-Extraktion
# =============================================================================
def get_room_name(device) -> Optional[str]:
    """Extrahiert Raumnamen aus Device."""
    room = getattr(device, 'room', None)
    if room:
        return getattr(room, 'name', None)
    return None


def extract_environment_sensor_data(sensor) -> Dict:
    """Umweltsensor: Temperatur, Luftfeuchtigkeit, CO2, PM2.5, VOC"""
    temp = getattr(sensor.attributes, 'current_temperature', None)
    return {k: v for k, v in {
        "device_id": sensor.id,
        "device_name": getattr(sensor.attributes, 'custom_name', None) or "Unknown",
        "device_type": "environment_sensor",
        "room": get_room_name(sensor),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "temperature": round(temp, 2) if temp is not None else None,
        "humidity": getattr(sensor.attributes, 'current_r_h', None),
        "pm25": getattr(sensor.attributes, 'current_p_m25', None),
        "co2": getattr(sensor.attributes, 'current_c_o2', None),
        "voc_index": getattr(sensor.attributes, 'voc_index', None),
    }.items() if v is not None}


def extract_motion_sensor_data(sensor) -> Dict:
    """Bewegungsmelder"""
    return {k: v for k, v in {
        "device_id": sensor.id,
        "device_name": getattr(sensor.attributes, 'custom_name', None) or "Unknown",
        "device_type": "motion_sensor",
        "room": get_room_name(sensor),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "is_detected": getattr(sensor.attributes, 'is_detected', None),
        "battery_percentage": getattr(sensor.attributes, 'battery_percentage', None),
    }.items() if v is not None}


def extract_open_close_sensor_data(sensor) -> Dict:
    """Tür-/Fenstersensor"""
    return {k: v for k, v in {
        "device_id": sensor.id,
        "device_name": getattr(sensor.attributes, 'custom_name', None) or "Unknown",
        "device_type": "open_close_sensor",
        "room": get_room_name(sensor),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "is_open": getattr(sensor.attributes, 'is_open', None),
        "battery_percentage": getattr(sensor.attributes, 'battery_percentage', None),
    }.items() if v is not None}


def extract_light_data(light) -> Dict:
    """Lampe: Status, Helligkeit, Farbtemperatur, Farbe"""
    hue = getattr(light.attributes, 'color_hue', None)
    sat = getattr(light.attributes, 'color_saturation', None)
    return {k: v for k, v in {
        "device_id": light.id,
        "device_name": getattr(light.attributes, 'custom_name', None) or "Unknown",
        "device_type": "light",
        "room": get_room_name(light),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "is_on": getattr(light.attributes, 'is_on', None),
        "brightness": getattr(light.attributes, 'light_level', None),
        "color_temperature": getattr(light.attributes, 'color_temperature', None),
        "color_hue": round(hue, 2) if hue is not None else None,
        "color_saturation": round(sat, 4) if sat is not None else None,
    }.items() if v is not None}


def extract_air_purifier_data(purifier) -> Dict:
    """Luftreiniger: Modus, Laufzeit, Filter, PM2.5"""
    fan_mode = getattr(purifier.attributes, 'fan_mode', None)
    return {k: v for k, v in {
        "device_id": purifier.id,
        "device_name": getattr(purifier.attributes, 'custom_name', None) or "Unknown",
        "device_type": "air_purifier",
        "room": get_room_name(purifier),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "fan_mode": fan_mode.value if fan_mode else None,
        "motor_state": getattr(purifier.attributes, 'motor_state', None),
        "motor_runtime": getattr(purifier.attributes, 'motor_runtime', None),
        "pm25": getattr(purifier.attributes, 'current_p_m25', None),
        "filter_alarm": getattr(purifier.attributes, 'filter_alarm_status', None),
        "filter_elapsed_time": getattr(purifier.attributes, 'filter_elapsed_time', None),
        "filter_lifetime": getattr(purifier.attributes, 'filter_lifetime', None),
    }.items() if v is not None}


def extract_outlet_data(outlet) -> Dict:
    """Steckdose: Status, Leistung, Strom, Spannung, Energie"""
    return {k: v for k, v in {
        "device_id": outlet.id,
        "device_name": getattr(outlet.attributes, 'custom_name', None) or "Unknown",
        "device_type": "outlet",
        "room": get_room_name(outlet),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "is_on": getattr(outlet.attributes, 'is_on', None),
        "power": getattr(outlet.attributes, 'current_active_power', None),
        "current": getattr(outlet.attributes, 'current_amps', None),
        "voltage": getattr(outlet.attributes, 'current_voltage', None),
        "energy_total": getattr(outlet.attributes, 'total_energy_consumed', None),
    }.items() if v is not None}


def extract_controller_data(controller) -> Dict:
    """Fernbedienung/Controller"""
    return {k: v for k, v in {
        "device_id": controller.id,
        "device_name": getattr(controller.attributes, 'custom_name', None) or "Unknown",
        "device_type": "controller",
        "room": get_room_name(controller),
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "battery_percentage": getattr(controller.attributes, 'battery_percentage', None),
        "is_on": getattr(controller.attributes, 'is_on', None),
    }.items() if v is not None}


# =============================================================================
# Polling
# =============================================================================
def poll_all_devices():
    """Pollt alle Geräte und publiziert nach MQTT."""
    global last_poll_time
    logger.info("Polling-Zyklus gestartet...")
    sent, skipped = 0, 0

    try:
        # Environment Sensors
        for sensor in hub.get_environment_sensors():
            device_cache[sensor.id] = sensor
            data = extract_environment_sensor_data(sensor)
            if should_poll_send(sensor.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/sensor/{sensor.id}", data, sensor.id)
                sent += 1
            else:
                skipped += 1

        # Motion Sensors
        for sensor in hub.get_occupancy_sensors():
            device_cache[sensor.id] = sensor
            data = extract_motion_sensor_data(sensor)
            if should_poll_send(sensor.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/motion/{sensor.id}", data, sensor.id)
                sent += 1
            else:
                skipped += 1

        # Open/Close Sensors
        for sensor in hub.get_open_close_sensors():
            device_cache[sensor.id] = sensor
            data = extract_open_close_sensor_data(sensor)
            if should_poll_send(sensor.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/door/{sensor.id}", data, sensor.id)
                sent += 1
            else:
                skipped += 1

        # Lights
        for light in hub.get_lights():
            device_cache[light.id] = light
            data = extract_light_data(light)
            if should_poll_send(light.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/light/{light.id}", data, light.id)
                sent += 1
            else:
                skipped += 1

        # Air Purifiers
        for purifier in hub.get_air_purifiers():
            device_cache[purifier.id] = purifier
            data = extract_air_purifier_data(purifier)
            if should_poll_send(purifier.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/purifier/{purifier.id}", data, purifier.id)
                sent += 1
            else:
                skipped += 1

        # Outlets
        for outlet in hub.get_outlets():
            device_cache[outlet.id] = outlet
            data = extract_outlet_data(outlet)
            if should_poll_send(outlet.id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/outlet/{outlet.id}", data, outlet.id)
                sent += 1
            else:
                skipped += 1

        # Controllers (nur einmal pro Basis-ID)
        seen_controllers = set()
        for controller in hub.get_controllers():
            base_id = controller.id.rsplit('_', 1)[0] if '_' in controller.id else controller.id
            if base_id in seen_controllers:
                continue
            seen_controllers.add(base_id)
            device_cache[controller.id] = controller
            data = extract_controller_data(controller)
            if should_poll_send(base_id, data):
                publish_to_mqtt(f"{MQTT_BASE_TOPIC}/controller/{base_id}", data, base_id)
                sent += 1
            else:
                skipped += 1

        logger.info(f"Polling: {sent} gesendet, {skipped} übersprungen, {len(device_cache)} Geräte")

    except Exception as e:
        logger.error(f"Polling-Fehler: {e}")

    last_poll_time = datetime.utcnow()


def polling_loop():
    """Endlos-Schleife für periodisches Polling."""
    while True:
        poll_all_devices()
        time.sleep(POLL_INTERVAL)


# =============================================================================
# WebSocket Event Listener
# =============================================================================
def handle_websocket_event(ws: Any, message: str):
    """Verarbeitet WebSocket Events vom Dirigera Hub."""
    try:
        event = json.loads(message)
        if event.get("type") != "deviceStateChanged":
            return

        event_data = event.get("data", {})
        device_id = event_data.get("id")
        attributes = event_data.get("attributes", {})

        if not device_id:
            return

        logger.debug(f"WebSocket: {device_id} - {list(attributes.keys())}")

        device_type = determine_device_type(device_id, attributes)
        data = build_event_data(device_id, device_type, attributes)

        # Topic bestimmen
        topic_map = {
            "environment_sensor": "sensor",
            "motion_sensor": "motion",
            "open_close_sensor": "door",
            "light": "light",
            "air_purifier": "purifier",
            "outlet": "outlet",
            "controller": "controller",
        }
        topic = f"{MQTT_BASE_TOPIC}/{topic_map.get(device_type, 'unknown')}/{device_id}"

        # Mit letzten Werten mergen
        if device_id in last_values:
            merged = last_values[device_id].copy()
            merged.update(data)
            data = merged

        publish_to_mqtt(topic, data, device_id, from_websocket=True)

    except json.JSONDecodeError as e:
        logger.error(f"JSON Parse Error: {e}")
    except Exception as e:
        logger.error(f"WebSocket Handler Error: {e}")


def build_event_data(device_id: str, device_type: str, attributes: Dict) -> Dict:
    """Baut Daten-Dict aus WebSocket Event Attributen."""
    data = {
        "device_id": device_id,
        "device_type": device_type,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    # Attribut-Mapping (API-Name -> unser Name)
    mapping = {
        "currentTemperature": "temperature",
        "currentRH": "humidity",
        "currentPM25": "pm25",
        "currentCO2": "co2",
        "vocIndex": "voc_index",
        "isDetected": "is_detected",
        "isOpen": "is_open",
        "batteryPercentage": "battery_percentage",
        "isOn": "is_on",
        "lightLevel": "brightness",
        "colorTemperature": "color_temperature",
        "colorHue": "color_hue",
        "colorSaturation": "color_saturation",
        "customName": "device_name",
        "fanMode": "fan_mode",
        "motorState": "motor_state",
        "motorRuntime": "motor_runtime",
        "filterAlarmStatus": "filter_alarm",
        "filterElapsedTime": "filter_elapsed_time",
        "filterLifetime": "filter_lifetime",
        "currentActivePower": "power",
        "currentAmps": "current",
        "currentVoltage": "voltage",
        "totalEnergyConsumed": "energy_total",
    }

    for api_name, our_name in mapping.items():
        if api_name in attributes:
            value = attributes[api_name]
            if our_name == "temperature" and value is not None:
                value = round(value, 2)
            elif our_name in ("color_hue", "color_saturation") and value is not None:
                value = round(value, 4)
            data[our_name] = value

    # Device Name aus Cache
    if "device_name" not in data and device_id in device_cache:
        cached = device_cache[device_id]
        data["device_name"] = getattr(cached.attributes, 'custom_name', None) or "Unknown"

    return data


def determine_device_type(device_id: str, attributes: Dict) -> str:
    """Bestimmt Device-Typ aus Cache oder Attributen."""
    # Aus Cache
    if device_id in device_cache:
        cached = device_cache[device_id]
        if hasattr(cached, 'attributes'):
            attrs = cached.attributes
            if hasattr(attrs, 'fan_mode'):
                return "air_purifier"
            if hasattr(attrs, 'current_temperature'):
                return "environment_sensor"
            if hasattr(attrs, 'is_detected'):
                return "motion_sensor"
            if hasattr(attrs, 'is_open'):
                return "open_close_sensor"
            if hasattr(attrs, 'current_active_power'):
                return "outlet"
            if hasattr(attrs, 'light_level'):
                return "light"
            if hasattr(attrs, 'battery_percentage') and not hasattr(attrs, 'is_detected'):
                return "controller"

    # Aus Event-Attributen raten
    if any(k in attributes for k in ["fanMode", "motorState", "filterAlarmStatus"]):
        return "air_purifier"
    if any(k in attributes for k in ["currentTemperature", "currentCO2"]):
        return "environment_sensor"
    if "isDetected" in attributes:
        return "motion_sensor"
    if "isOpen" in attributes:
        return "open_close_sensor"
    if any(k in attributes for k in ["currentActivePower", "currentAmps"]):
        return "outlet"
    if "lightLevel" in attributes or "colorTemperature" in attributes:
        return "light"

    return "unknown"


def start_websocket_listener():
    """Startet WebSocket mit Auto-Reconnect."""
    while True:
        try:
            logger.info("Starte WebSocket Listener...")
            hub.create_event_listener(
                on_message=handle_websocket_event,
                on_error=lambda ws, e: logger.error(f"WebSocket Fehler: {e}"),
            )
        except Exception as e:
            logger.error(f"WebSocket abgestürzt: {e}")
            logger.info("Reconnect in 10s...")
            time.sleep(10)


# =============================================================================
# Main
# =============================================================================
def main():
    global mqtt_client, hub

    if not DIRIGERA_TOKEN:
        logger.error("DIRIGERA_TOKEN nicht gesetzt!")
        logger.error("Token generieren: python -c 'import dirigera; print(dirigera.generate_token(\"IP\"))'")
        return

    logger.info("=" * 60)
    logger.info("DIRIGERA MQTT Bridge")
    logger.info("=" * 60)
    logger.info(f"Dirigera Hub:   {DIRIGERA_IP}")
    logger.info(f"MQTT Broker:    {MQTT_HOST}:{MQTT_PORT}")
    logger.info(f"Base Topic:     {MQTT_BASE_TOPIC}")
    logger.info(f"Poll Interval:  {POLL_INTERVAL}s")
    logger.info("=" * 60)

    # Verbindungen
    hub = dirigera.Hub(token=DIRIGERA_TOKEN, ip_address=DIRIGERA_IP)
    mqtt_client = create_mqtt_client()
    time.sleep(2)

    # Initiales Polling
    logger.info("Initiales Polling...")
    poll_all_devices()

    # Polling-Thread
    polling_thread = threading.Thread(target=polling_loop, name="Polling", daemon=True)
    polling_thread.start()

    # WebSocket (blockiert)
    start_websocket_listener()


if __name__ == "__main__":
    main()
