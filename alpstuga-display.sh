#!/bin/bash
# ALPSTUGA Display ein-/ausschalten via Docker Container
# Nutzung: ./alpstuga-display.sh on|off|toggle|status|list [--name TEILNAME]
docker exec dirigera-bridge python /app/alpstuga-display.py "$@"
