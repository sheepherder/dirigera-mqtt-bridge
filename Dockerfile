FROM python:3.11-slim

WORKDIR /app

# Abhängigkeiten
RUN pip install --no-cache-dir \
    dirigera>=1.2.6 \
    paho-mqtt>=2.0.0 \
    websocket-client>=1.6.0

COPY bridge.py /app/bridge.py

# Health Check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "python.*bridge.py" || exit 1

# Nicht als root
RUN useradd -m -u 1000 bridge
USER bridge

ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "/app/bridge.py"]
