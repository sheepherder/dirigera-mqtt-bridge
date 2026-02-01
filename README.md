# Dirigera MQTT Bridge

Verbindet den **IKEA DIRIGERA Hub** mit einem **MQTT Broker**.

Für alle die ihre IKEA Smart Home Geräte in eigene Systeme integrieren wollen - ohne Home Assistant.

```
┌──────────────┐      WebSocket       ┌──────────────────┐      MQTT       ┌──────────────┐
│   DIRIGERA   │  ◄─────────────────► │  dirigera-mqtt-  │ ─────────────►  │    MQTT      │
│     Hub      │      + Polling       │      bridge      │                 │   Broker     │
└──────────────┘                      └──────────────────┘                 └──────────────┘
                                                                                  │
                                              ┌───────────────────────────────────┤
                                              ▼                                   ▼
                                        ┌──────────┐                       ┌──────────────┐
                                        │ Telegraf │ ───────────────────►  │   InfluxDB   │
                                        └──────────┘                       └──────────────┘
                                                                                  │
                                                                                  ▼
                                                                           ┌──────────────┐
                                                                           │   Grafana    │
                                                                           └──────────────┘
```

## Features

- **WebSocket Event Listener** für Echtzeit-Updates
- **Periodisches Polling** als Backup (konfigurierbar)
- **Deduplizierung** um redundante Updates zu filtern
- **Docker-ready** mit Health Check

> **Hinweis:** Diese Bridge ist aktuell **nur lesend** - Gerätedaten werden nach MQTT publiziert, Steuerung (Lampen schalten, etc.) ist nicht implementiert.

## Unterstützte Geräte

| Gerätetyp | MQTT Topic | Messwerte |
|-----------|------------|-----------|
| Umweltsensor (VINDSTYRKA) | `dirigera/sensor/{id}` | temperature, humidity, pm25, co2, voc_index |
| Bewegungsmelder (VALLHORN) | `dirigera/motion/{id}` | is_detected, battery_percentage |
| Tür-/Fenstersensor (PARASOLL) | `dirigera/door/{id}` | is_open, battery_percentage |
| Lampen (TRÅDFRI etc.) | `dirigera/light/{id}` | is_on, brightness, color_temperature, color_hue/sat |
| Luftreiniger (STARKVIND) | `dirigera/purifier/{id}` | fan_mode, motor_state, pm25, filter_alarm |
| Steckdosen (INSPELNING) | `dirigera/outlet/{id}` | is_on, power, current, voltage, energy_total |
| Fernbedienungen | `dirigera/controller/{id}` | battery_percentage, is_on |

## Installation

### 1. Dirigera Token generieren

```bash
pip install dirigera

python -c "import dirigera; print(dirigera.generate_token('DIRIGERA_IP'))"
```

Dann den Pairing-Knopf am Dirigera Hub drücken (unten am Gerät).

### 2. Docker Compose

```bash
git clone https://github.com/sheepherder/dirigera-mqtt-bridge.git
cd dirigera-mqtt-bridge

cp .env.example .env
# .env bearbeiten: DIRIGERA_IP und DIRIGERA_TOKEN eintragen

docker compose up -d
```

### 3. Logs prüfen

```bash
docker compose logs -f
```

## Konfiguration

Alle Einstellungen via Umgebungsvariablen:

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `DIRIGERA_IP` | - | IP-Adresse des Dirigera Hub (Pflicht) |
| `DIRIGERA_TOKEN` | - | Auth-Token (Pflicht, siehe oben) |
| `MQTT_HOST` | localhost | MQTT Broker Host |
| `MQTT_PORT` | 1883 | MQTT Broker Port |
| `MQTT_USER` | - | MQTT Username (optional) |
| `MQTT_PASSWORD` | - | MQTT Passwort (optional) |
| `MQTT_BASE_TOPIC` | dirigera | Prefix für alle Topics |
| `POLL_INTERVAL` | 300 | Polling-Intervall in Sekunden |
| `DEDUP_WINDOW` | 5 | Zeitfenster für Deduplizierung in Sekunden |
| `LOG_LEVEL` | INFO | Log-Level (DEBUG, INFO, WARNING, ERROR) |

## MQTT Nachrichtenformat

Alle Nachrichten sind JSON:

```json
{
  "device_id": "abc123-def456",
  "device_name": "Wohnzimmer Sensor",
  "device_type": "environment_sensor",
  "room": "Wohnzimmer",
  "timestamp": "2026-01-31T12:34:56.789Z",
  "temperature": 21.5,
  "humidity": 45,
  "pm25": 12,
  "voc_index": 150
}
```

---

## Integration mit Telegraf + InfluxDB + Grafana

Diese Bridge ist ideal für den TIG-Stack (Telegraf → InfluxDB → Grafana).

### Telegraf Konfiguration

```toml
# =============================================================================
# Dirigera MQTT Input
# =============================================================================
[[inputs.mqtt_consumer]]
  servers = ["tcp://localhost:1883"]
  topics = [
    "dirigera/sensor/#",
    "dirigera/motion/#",
    "dirigera/door/#",
    "dirigera/light/#",
    "dirigera/purifier/#",
    "dirigera/outlet/#",
    "dirigera/controller/#"
  ]
  client_id = "telegraf-dirigera"
  qos = 1
  persistent_session = true
  data_format = "json_v2"

  [[inputs.mqtt_consumer.json_v2]]
    measurement_name = "dirigera"

    # Tags (für Filterung in Grafana)
    [[inputs.mqtt_consumer.json_v2.tag]]
      path = "device_id"
      optional = true
    [[inputs.mqtt_consumer.json_v2.tag]]
      path = "device_name"
      optional = true
    [[inputs.mqtt_consumer.json_v2.tag]]
      path = "device_type"
      optional = true
    [[inputs.mqtt_consumer.json_v2.tag]]
      path = "room"
      optional = true

    # Umweltsensoren
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "temperature"
      type = "float"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "humidity"
      type = "int"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "pm25"
      type = "int"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "co2"
      type = "int"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "voc_index"
      type = "int"
      optional = true

    # Bewegungs-/Türsensoren
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "is_detected"
      type = "bool"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "is_open"
      type = "bool"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "battery_percentage"
      type = "int"
      optional = true

    # Lampen
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "is_on"
      type = "bool"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "brightness"
      type = "float"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "color_temperature"
      type = "int"
      optional = true

    # Luftreiniger
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "fan_mode"
      type = "string"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "motor_state"
      type = "int"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "filter_alarm"
      type = "bool"
      optional = true

    # Steckdosen
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "power"
      type = "float"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "current"
      type = "float"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "voltage"
      type = "float"
      optional = true
    [[inputs.mqtt_consumer.json_v2.field]]
      path = "energy_total"
      type = "float"
      optional = true

# =============================================================================
# InfluxDB Output
# =============================================================================
[[outputs.influxdb_v2]]
  urls = ["http://localhost:8086"]
  token = "DEIN_INFLUXDB_TOKEN"
  organization = "home"
  bucket = "smarthome"
```

### InfluxDB

```yaml
# docker-compose.yml Ergänzung
influxdb:
  image: influxdb:2.8
  container_name: influxdb
  ports:
    - "8086:8086"
  volumes:
    - ./influxdb/data:/var/lib/influxdb2
  environment:
    - DOCKER_INFLUXDB_INIT_MODE=setup
    - DOCKER_INFLUXDB_INIT_USERNAME=admin
    - DOCKER_INFLUXDB_INIT_PASSWORD=sicheres_passwort
    - DOCKER_INFLUXDB_INIT_ORG=home
    - DOCKER_INFLUXDB_INIT_BUCKET=smarthome
    - DOCKER_INFLUXDB_INIT_RETENTION=4w
```

### Grafana

```yaml
# docker-compose.yml Ergänzung
grafana:
  image: grafana/grafana-oss:latest
  container_name: grafana
  ports:
    - "3000:3000"
  volumes:
    - ./grafana/data:/var/lib/grafana
  depends_on:
    - influxdb
```

### Beispiel Flux Query (Grafana)

```flux
from(bucket: "smarthome")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "dirigera")
  |> filter(fn: (r) => r.device_type == "environment_sensor")
  |> filter(fn: (r) => r._field == "temperature")
  |> aggregateWindow(every: 5m, fn: mean)
```

---

## Debugging

### MQTT mitlesen

```bash
# Alle Dirigera Topics
mosquitto_sub -h localhost -t 'dirigera/#' -v

# Nur Sensoren
mosquitto_sub -h localhost -t 'dirigera/sensor/#' -v
```

### Bridge Logs

```bash
# Normal
docker compose logs -f dirigera-bridge

# Debug-Modus
LOG_LEVEL=DEBUG docker compose up
```

---

## Unterschied zu anderen Lösungen

| Lösung | Beschreibung |
|--------|--------------|
| **Home Assistant Dirigera Integration** | Voll integriert in HA, braucht aber HA |
| **Diese Bridge** | Standalone, reines MQTT, für eigene Stacks |
| **Node-RED Dirigera Nodes** | Grafisch, aber mehr Overhead |

---

## Lizenz

MIT License - siehe [LICENSE](LICENSE)
