#!/usr/bin/env python3
"""
ALPSTUGA Display Control

Steuert das Display der IKEA ALPSTUGA Luftqualitätssensoren über den DIRIGERA Hub.
Das Display erscheint im Hub als Outlet-Gerät mit isOn-Attribut (Matter OnOff-Cluster).

Nutzung:
    python alpstuga-display.py on|off|toggle|status|list [--name TEILNAME]
"""

import argparse
import json
import os
import sys

import dirigera


def connect_hub():
    """Verbindet mit dem DIRIGERA Hub."""
    token = os.environ.get("DIRIGERA_TOKEN")
    ip = os.environ.get("DIRIGERA_IP", "192.168.0.1")

    if not token:
        print("Fehler: DIRIGERA_TOKEN nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    return dirigera.Hub(token=token, ip_address=ip)


def find_alpstuga_displays(hub):
    """
    Findet ALPSTUGA Display-Geräte im Hub.

    Das Display ist ein steuerbares Attribut (isOn) direkt am environmentSensor.
    Filterkriterien:
    - deviceType == "environmentSensor"
    - Modell oder Name enthält "ALPSTUGA" (case-insensitive)
    - Gerät hat isOn in capabilities.canReceive
    """
    all_devices = hub.get("/devices")
    displays = []

    for device in all_devices:
        attrs = device.get("attributes", {})
        name = attrs.get("customName", "")
        model = attrs.get("model", "")
        device_type = device.get("deviceType", "")

        # ALPSTUGA = environmentSensor mit ALPSTUGA im Modell oder Namen
        if device_type != "environmentSensor":
            continue
        if "alpstuga" not in name.lower() and "alpstuga" not in model.lower():
            continue

        # isOn-Capability prüfen
        can_receive = device.get("capabilities", {}).get("canReceive", [])
        if "isOn" not in can_receive:
            continue

        room_name = None
        room = device.get("room", {})
        if room:
            room_name = room.get("name")

        displays.append({
            "id": device["id"],
            "name": name,
            "room": room_name,
            "is_on": attrs.get("isOn"),
        })

    return displays


def select_display(displays, name_filter=None):
    """
    Wählt ein ALPSTUGA Display aus.

    - Bei --name: Teilstring-Match (case-insensitive)
    - Bei einem Gerät: Auto-Auswahl
    - Bei mehreren: Nummerierte Liste, User wählt
    """
    if not displays:
        print("Keine ALPSTUGA-Displays gefunden.", file=sys.stderr)
        print("Tipp: 'list' zeigt alle gefundenen Geräte.", file=sys.stderr)
        sys.exit(1)

    # Filter by name
    if name_filter:
        matches = [d for d in displays if name_filter.lower() in d["name"].lower()]
        if not matches:
            # Auch im Raumnamen suchen
            matches = [d for d in displays
                       if d["room"] and name_filter.lower() in d["room"].lower()]
        if not matches:
            print(f"Kein ALPSTUGA mit '{name_filter}' gefunden.", file=sys.stderr)
            print("Verfügbar:", file=sys.stderr)
            for d in displays:
                room = f" ({d['room']})" if d["room"] else ""
                print(f"  - {d['name']}{room}", file=sys.stderr)
            sys.exit(1)
        if len(matches) == 1:
            return matches[0]
        displays = matches

    # Auto-select if only one
    if len(displays) == 1:
        return displays[0]

    # Interactive selection
    print("Mehrere ALPSTUGA-Displays gefunden:")
    for i, d in enumerate(displays, 1):
        room = f" ({d['room']})" if d["room"] else ""
        status = "AN" if d["is_on"] else "AUS"
        print(f"  [{i}] {d['name']}{room} - Display {status}")

    try:
        choice = input("Auswahl (Nummer): ").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(displays):
            return displays[idx]
        print("Ungültige Auswahl.", file=sys.stderr)
        sys.exit(1)
    except (ValueError, EOFError, KeyboardInterrupt):
        print("\nAbgebrochen.", file=sys.stderr)
        sys.exit(1)


def cmd_list(hub):
    """Listet alle ALPSTUGA-Displays auf."""
    displays = find_alpstuga_displays(hub)
    if not displays:
        print("Keine ALPSTUGA-Displays gefunden.")
        return

    print(f"{len(displays)} ALPSTUGA-Display(s) gefunden:\n")
    for d in displays:
        room = f"  Raum: {d['room']}" if d["room"] else ""
        status = "AN" if d["is_on"] else "AUS"
        print(f"  {d['name']}")
        if room:
            print(f"  {room}")
        print(f"  Display: {status}")
        print(f"  ID: {d['id']}")
        print()


def cmd_status(hub, name_filter=None):
    """Zeigt den Display-Status."""
    displays = find_alpstuga_displays(hub)
    display = select_display(displays, name_filter)
    room = f" ({display['room']})" if display["room"] else ""
    status = "AN" if display["is_on"] else "AUS"
    print(f"{display['name']}{room}: Display {status}")


def cmd_set(hub, turn_on, name_filter=None):
    """Schaltet das Display ein oder aus."""
    displays = find_alpstuga_displays(hub)
    display = select_display(displays, name_filter)

    hub.patch(
        route=f"/devices/{display['id']}",
        data=[{"attributes": {"isOn": turn_on}}],
    )

    room = f" ({display['room']})" if display["room"] else ""
    action = "eingeschaltet" if turn_on else "ausgeschaltet"
    print(f"{display['name']}{room}: Display {action}")


def cmd_toggle(hub, name_filter=None):
    """Schaltet das Display um (toggle)."""
    displays = find_alpstuga_displays(hub)
    display = select_display(displays, name_filter)
    new_state = not display["is_on"]

    hub.patch(
        route=f"/devices/{display['id']}",
        data=[{"attributes": {"isOn": new_state}}],
    )

    room = f" ({display['room']})" if display["room"] else ""
    action = "eingeschaltet" if new_state else "ausgeschaltet"
    print(f"{display['name']}{room}: Display {action}")


def main():
    parser = argparse.ArgumentParser(
        description="ALPSTUGA Display ein-/ausschalten",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Beispiele:
  %(prog)s status              Status anzeigen
  %(prog)s on                  Display einschalten
  %(prog)s off                 Display ausschalten
  %(prog)s toggle              Display umschalten
  %(prog)s list                Alle ALPSTUGAs auflisten
  %(prog)s off --name Küche    Bestimmtes Gerät wählen""",
    )
    parser.add_argument(
        "command",
        choices=["on", "off", "toggle", "status", "list"],
        help="Aktion: on, off, toggle, status, list",
    )
    parser.add_argument(
        "--name", "-n",
        help="ALPSTUGA nach Name oder Raum filtern (Teilstring)",
    )

    args = parser.parse_args()
    hub = connect_hub()

    if args.command == "list":
        cmd_list(hub)
    elif args.command == "status":
        cmd_status(hub, args.name)
    elif args.command == "on":
        cmd_set(hub, True, args.name)
    elif args.command == "off":
        cmd_set(hub, False, args.name)
    elif args.command == "toggle":
        cmd_toggle(hub, args.name)


if __name__ == "__main__":
    main()
