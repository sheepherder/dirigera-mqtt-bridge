#!/usr/bin/env python3
"""
DIRIGERA Firmware Check

Prüft Firmware-Versionen aller Geräte im DIRIGERA Hub und benachrichtigt
per ntfy bei Änderungen. Speichert den letzten Stand in einem JSON State-File.

Nutzung:
    python firmware-check.py [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

import dirigera

STATE_FILE = os.environ.get("FIRMWARE_STATE_FILE", "/data/firmware-state.json")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
DIRIGERA_TOKEN = os.environ.get("DIRIGERA_TOKEN", "")
DIRIGERA_IP = os.environ.get("DIRIGERA_IP", "192.168.0.1")

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_device_firmware(hub):
    """Holt Firmware-Infos aller Geräte vom Hub."""
    devices = hub.get("/devices")
    result = {}

    for device in devices:
        device_id = device.get("id", "")
        attrs = device.get("attributes", {})
        firmware = attrs.get("firmwareVersion", "")
        if not firmware:
            continue

        result[device_id] = {
            "name": attrs.get("customName", attrs.get("model", "Unbekannt")),
            "type": device.get("deviceType", "unknown"),
            "model": attrs.get("model", ""),
            "firmware": firmware,
            "ota_status": attrs.get("otaStatus", ""),
        }

    return result


def load_state():
    """Lädt gespeicherte Firmware-Versionen (Dict device_id → info)."""
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data.get("devices", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(devices):
    """Speichert aktuelle Firmware-Versionen."""
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"devices": devices}, f, indent=2, ensure_ascii=False)
    logger.info(f"State gespeichert: {STATE_FILE}")


def find_changes(old_devices, current):
    """Vergleicht alten und neuen Stand, gibt Änderungen zurück."""
    changes = []

    for device_id, info in current.items():
        old_info = old_devices.get(device_id)
        if old_info is None:
            changes.append({
                "name": info["name"],
                "model": info["model"],
                "type": "new",
                "firmware": info["firmware"],
            })
        elif old_info.get("firmware") != info["firmware"]:
            changes.append({
                "name": info["name"],
                "model": info["model"],
                "type": "update",
                "old_firmware": old_info["firmware"],
                "firmware": info["firmware"],
            })

    for device_id, old_info in old_devices.items():
        if device_id not in current:
            changes.append({
                "name": old_info["name"],
                "model": old_info.get("model", ""),
                "type": "removed",
                "firmware": old_info["firmware"],
            })

    return changes


def format_device(c):
    """Formatiert Gerätename mit Model wenn abweichend."""
    name = c["name"]
    model = c.get("model", "")
    if model and model.lower() not in name.lower():
        return f"{name} ({model})"
    return name


def format_notification(changes, is_initial=False):
    """Formatiert die ntfy-Nachricht."""
    lines = []
    for c in changes:
        label = format_device(c)
        if is_initial:
            lines.append(f"{label}: {c['firmware']}")
        elif c.get("type") == "update":
            lines.append(f"{label}: {c['old_firmware']} → {c['firmware']}")
        elif c.get("type") == "new":
            lines.append(f"{label}: neu ({c['firmware']})")
        elif c.get("type") == "removed":
            lines.append(f"{label}: entfernt")

    return "\n".join(sorted(lines))


def send_ntfy(title, body, dry_run=False):
    """Sendet Notification an ntfy."""
    if not NTFY_TOPIC:
        logger.warning("NTFY_TOPIC nicht gesetzt, überspringe Notification")
        return False

    url = f"{NTFY_SERVER.rstrip('/')}/{NTFY_TOPIC}"

    if dry_run:
        logger.info(f"[DRY-RUN] Würde senden an {url}:")
        logger.info(f"  Title: {title}")
        for line in body.split("\n"):
            logger.info(f"  {line}")
        return True

    try:
        req = Request(url, data=body.encode("utf-8"), method="POST")
        req.add_header("Title", title)
        req.add_header("Priority", "3")
        req.add_header("Tags", "package")
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info(f"Notification gesendet: {title}")
                return True
            else:
                logger.warning(f"ntfy Antwort: {resp.status}")
                return False
    except URLError as e:
        logger.warning(f"ntfy nicht erreichbar: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="DIRIGERA Firmware Check")
    parser.add_argument("--dry-run", action="store_true",
                        help="Zeigt Änderungen ohne Notification zu senden")
    args = parser.parse_args()

    if not DIRIGERA_TOKEN:
        logger.error("DIRIGERA_TOKEN nicht gesetzt!")
        sys.exit(1)

    # Hub verbinden und Firmware-Daten holen
    try:
        hub = dirigera.Hub(token=DIRIGERA_TOKEN, ip_address=DIRIGERA_IP)
        current = get_device_firmware(hub)
    except Exception as e:
        logger.error(f"Hub nicht erreichbar: {e}")
        sys.exit(1)

    logger.info(f"{len(current)} Geräte mit Firmware-Info gefunden")

    old_devices = load_state()
    changes = find_changes(old_devices, current)

    if changes:
        is_initial = not old_devices
        title = "DIRIGERA Firmware-Stand" if is_initial else "DIRIGERA Firmware Update"
        logger.info(f"{len(changes)} Firmware-Änderung(en) erkannt")
        send_ntfy(title, format_notification(changes, is_initial), dry_run=args.dry_run)
    else:
        logger.info("Keine Firmware-Änderungen")

    # State speichern (auch bei ntfy-Fehler, damit nicht erneut getriggert wird)
    if not args.dry_run:
        save_state(current)


if __name__ == "__main__":
    main()
