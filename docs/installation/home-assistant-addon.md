# Home Assistant Add-on Pilot

INS-EI runs as a separate Home Assistant add-on process. Restarting INS-EI does not require restarting Home Assistant.

The initial pilot is strictly read-only and always starts in SHADOW mode. It uses the Supervisor-provided Home Assistant API token and does not require a user to store a long-lived HA token.

## Installation

Add this GitHub repository as a Home Assistant add-on repository, refresh the add-on store, install **INS-EI Pilot**, configure mappings, then start the add-on and inspect its log.

The first pilot runtime only reads mapped entities, normalizes them and reports collection/data-quality counts. It has no action/write implementation.

## Mapping

Mappings are configured in the add-on options. Example fields:

- component_id
- point
- entity_id
- unit (optional)
- role
- scale (optional)
- invert_sign (optional)
- max_age_seconds (optional)

Customer entity IDs remain local to the installation and are not committed to the public repository.
