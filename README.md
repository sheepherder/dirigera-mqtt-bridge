# Dirigera MQTT Bridge

Liefert Daten aus dem **IKEA DIRIGERA Hub** an einen **MQTT Broker**.

Funktioniert mit dem aktuellen IKEA Smart Home System (Matter-basiert, seit 2024).

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
- **Erreichbarkeitsüberwachung** mit MQTT-Alerts bei Geräteausfällen
- **Deduplizierung** um redundante Updates zu filtern
- **Firmware-Update-Notifier** per ntfy Push-Notification
- **Docker-ready** mit Health Check

> **Hinweis:** Diese Bridge ist aktuell **nur lesend** - Gerätedaten werden nach MQTT publiziert, Steuerung (Lampen schalten, etc.) ist nicht implementiert.

### Funktionsweise im Detail

**WebSocket vs. Polling:**

Der Dirigera Hub liefert Echtzeit-Updates per WebSocket, aber diese enthalten nur geänderte Attribute (z.B. nur `temperature`, nicht den Raumnamen). Deshalb:

1. **Initiales Polling** beim Start: Alle Geräte werden abgefragt und in einen Cache geladen (inkl. Metadaten wie `device_name`, `room`, `device_type`)
2. **WebSocket Events** liefern Änderungen in Echtzeit, werden mit Cache-Daten angereichert und sofort nach MQTT gepusht
3. **Periodisches Polling** (alle 5 Min) sendet nur dann nach MQTT, wenn für das Gerät seit dem letzten Poll kein WebSocket-Update kam

**Welche Geräte senden WebSocket Events?**

- **Umweltsensoren:** Senden alle paar Sekunden Updates (Temperatur, Luftqualität ändern sich ständig). ALPSTUGA hat CO2, VINDSTYRKA hat VOC statt CO2.
- **Bewegungsmelder, Tür-/Fenstersensoren:** Senden bei Zustandsänderung (Bewegung erkannt, Tür geöffnet)
- **Lampen:** Senden bei Ein/Aus, Helligkeitsänderung
- **Luftreiniger:** Senden bei Modusänderung, Filterstatus
- **Fernbedienungen, Steckdosen:** Senden selten oder nie WebSocket-Updates → werden primär über Polling erfasst

**Erreichbarkeitsüberwachung:**

Der Dirigera Hub pflegt pro Gerät ein `is_reachable`-Flag. Bekanntes Problem: Einzelne Sensoren (v.a. ALPSTUGA über Matter/Thread) verlieren sporadisch ihre Verbindung zum Hub — der Hub cached dann die letzten Werte und liefert sie als wären sie aktuell. Das Display des Sensors zeigt dabei weiterhin korrekte Live-Werte, weil es lokal misst.

Die Bridge erkennt das und reagiert:

1. **Beim Polling** wird `is_reachable` jedes Geräts geprüft. Nicht erreichbare Geräte werden **nicht** nach MQTT publiziert, damit keine eingefrorenen/veralteten Werte in InfluxDB landen
2. **MQTT-Alert** wird bei Statuswechsel an `dirigera/bridge/alert/{device_id}` gepubliziert (retained), z.B.:
   ```json
   {
     "device_id": "9fcc32a3-..._1",
     "device_name": "ALPSTUGA 1",
     "event": "unreachable",
     "message": "ALPSTUGA 1 ist nicht erreichbar. Sensor muss ggf. aus- und wieder eingesteckt werden.",
     "timestamp": "2026-03-22T07:30:00.000Z"
   }
   ```
3. **Recovery-Versuch** per Matter Identify Cluster (`PUT /v1/devices/{id}/identify`) — der Hub sendet einen Identify-Befehl an den Sensor. Dies läuft in einem eigenen Thread, um das Polling nicht zu blockieren. Bei echtem Thread-Verbindungsabbruch hilft dies nicht — der Sensor muss physisch aus- und wieder eingesteckt werden.
4. Wenn der Sensor wieder erreichbar ist, wird eine `"event": "reachable"`-Nachricht gesendet und im Log `ONLINE: {name} ist wieder erreichbar` ausgegeben

> **Hintergrund:** Der Dirigera Hub verliert sporadisch (ca. alle paar Wochen) die Thread-Verbindung zu einzelnen Sensoren. Dies ist ein bekanntes Firmware-Problem ([dirigera#113](https://github.com/Leggin/dirigera/issues/113)). Der Sensor selbst funktioniert weiter, aber der Hub bekommt keine Updates mehr und gibt bei API-Abfragen eingefrorene Werte zurück. Bisher gibt es keinen API-Weg, die Verbindung remote wiederherzustellen.

**Deduplizierung:**

Wenn ein Gerät innerhalb von 5 Sekunden mehrfach identische Werte sendet, wird nur das erste Update nach MQTT gepusht.

## Unterstützte Geräte

| Gerätetyp | MQTT Topic | Messwerte |
|-----------|------------|-----------|
| Umweltsensor (ALPSTUGA, VINDSTYRKA) | `dirigera/sensor/{id}` | temperature, humidity, pm25, co2 (ALPSTUGA) oder voc_index (VINDSTYRKA) |
| Bewegungsmelder (MYGGSPRAY) | `dirigera/motion/{id}` | is_detected, battery_percentage |
| Tür-/Fenstersensor (MYGGBETT) | `dirigera/door/{id}` | is_open, battery_percentage |
| Lampen (KAJPLATS) | `dirigera/light/{id}` | is_on, brightness, color_temperature, color_hue/sat |
| Luftreiniger (STARKVIND) | `dirigera/purifier/{id}` | fan_mode, motor_state, pm25, filter_alarm |
| Steckdosen (TRÅDFRI) | `dirigera/outlet/{id}` | is_on, power, current, voltage, energy_total |
| Fernbedienungen (TRÅDFRI, BILRESA) | `dirigera/controller/{id}` | battery_percentage, is_on |
| **Bridge Alerts** | `dirigera/bridge/alert/{id}` | device_name, event (unreachable/reachable), message |

Andere Geräte funktionieren vermutlich auch. Matter-Geräte anderer Hersteller werden ebenfalls unterstützt, sofern sie am Dirigera Hub angemeldet sind.

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

docker-compose up -d
```

### 3. Logs prüfen

```bash
docker logs dirigera-bridge -f -n 20
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
| `NTFY_TOPIC` | - | ntfy Topic für Firmware-Notifications (Pflicht für Firmware-Check) |
| `NTFY_SERVER` | https://ntfy.sh | ntfy Server URL |

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

# Bridge Alerts (Geräteausfälle)
mosquitto_sub -h localhost -t 'dirigera/bridge/alert/#' -v
```

### Bridge Logs

```bash
docker logs dirigera-bridge -f -n 50
```

Für Debug-Modus: `LOG_LEVEL=DEBUG` in `.env` setzen und Container neu starten.

### Erreichbarkeits-Logs

Wenn ein Gerät offline geht:
```
WARNING - OFFLINE: ALPSTUGA 1 ist nicht erreichbar (is_reachable=False)
INFO    - [Recovery] Recovery-Versuch: Sende Identify an ALPSTUGA 1...
INFO    - [Recovery] Identify an ALPSTUGA 1 gesendet (202 Accepted)
INFO    - Polling: 17 gesendet, 0 übersprungen, 1 offline, 18 Geräte
```

Wenn ein Gerät wieder online ist (z.B. nach Aus-/Einstecken):
```
INFO    - ONLINE: ALPSTUGA 1 ist wieder erreichbar
```

---

## Firmware-Update-Notifier

Überwacht die Firmware-Versionen aller Geräte im DIRIGERA Hub (inkl. Hub selbst) und sendet Push-Notifications per [ntfy](https://ntfy.sh) bei Änderungen. IKEA liefert Firmware-Updates silent über die App aus — ohne Changelog und ohne Benachrichtigung.

### Einrichtung

1. ntfy-App auf dem Handy installieren ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347))
2. `NTFY_TOPIC` in `.env` setzen (z.B. `mein_prefix_dirigera_fw` — der Topicname ist gleichzeitig das "Passwort")
3. In der ntfy-App den Topic subscriben
4. Container neu bauen: `docker-compose build && docker-compose up -d`

### Nutzung

```bash
# Manueller Test (dry-run, keine Notification)
./firmware-check.sh --dry-run

# Manueller Lauf (sendet Notification bei Änderungen)
./firmware-check.sh

# systemd-Timer installieren (prüft alle 6h automatisch)
sudo ./install-dirigera-firmware-check.sh

# Timer-Status prüfen
systemctl list-timers dirigera-*

# Timer deinstallieren
sudo ./uninstall-dirigera-firmware-check.sh
```

### Was wird überwacht?

- Firmware-Version aller Geräte (Hub, Sensoren, Lampen, Fernbedienungen, ...)
- Neue Geräte (z.B. nach Pairing)
- Entfernte Geräte

Beim ersten Lauf wird eine Übersicht aller Geräte und ihrer Versionen gesendet. Danach nur noch bei Änderungen.

### Beispiel-Notification

```
DIRIGERA Firmware Update
ALPSTUGA 1: 1.0.15 → 1.0.16
Zuhause: 2.934.0 → 2.935.0
```

---

## ALPSTUGA Display-Steuerung

Das Display der ALPSTUGA Luftqualitätssensoren kann per Kommandozeile ein- und ausgeschaltet werden. Im DIRIGERA Hub hat der Sensor ein steuerbares `isOn`-Attribut (Matter OnOff-Cluster mit DeadFrontBehavior), das das Display kontrolliert.

### Nutzung (vom Host)

```bash
# Alle ALPSTUGAs auflisten
./alpstuga-display.sh list

# Display-Status anzeigen
./alpstuga-display.sh status

# Display ein-/ausschalten
./alpstuga-display.sh on
./alpstuga-display.sh off

# Umschalten (toggle)
./alpstuga-display.sh toggle

# Bestimmtes Gerät wählen (nach Name oder Raum)
./alpstuga-display.sh off --name "Schlafzimmer"
./alpstuga-display.sh on -n Küche
```

Bei mehreren ALPSTUGAs wird ohne `--name` interaktiv eine nummerierte Liste angezeigt. Bei nur einem Gerät wird es automatisch gewählt.

> **Hinweis:** Der Container `dirigera-bridge` muss laufen. Das Script wird per `docker exec` im Container ausgeführt.

---

## Lizenz

MIT License - siehe [LICENSE](LICENSE)
