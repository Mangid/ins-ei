# INS-EI Server

Central backend for INS Energy Intelligence.

## Components

- FastAPI backend
- MCP interface
- SQLite for customers, installations and operational metadata
- InfluxDB for long-term telemetry
- OekoFEN integration
- Home Assistant / INS-EI telemetry ingestion

## Security

Runtime credentials are mounted from `server/secrets/`.

The `secrets/` directory and runtime databases must never be committed.

## Architecture

INS-EI clients send normalized telemetry to the central server.

The server stores:
- customer and installation metadata in SQLite
- time-series telemetry in InfluxDB

The server API provides current installation state and analysis data to the INS-EI frontend and other services.
