"""Known OekoFEN semantic profiles from existing INS installations."""

# Canonical semantic names are deliberately independent from Home Assistant
# entity IDs. Existing ins_okofen adapters may feed these values directly.
PELLEMATIC_BUFFER_DHW = {
    "family": "Pellematic",
    "capabilities": {
        "pellet_boiler", "buffer", "dhw", "heating_circuits",
        "fuel_accounting",
    },
    "aliases": {
        "puffer_oben": "buffer_temperature_upper",
        "puffer_zweiter_fuhler_2": "buffer_temperature_lower",
        "warmwasser": "dhw_temperature",
        "kesseltemperatur": "boiler_temperature",
        "kessel_modulation": "current_power",
        "brennerstarts": "burner_starts",
        "pellets_heute": "fuel_consumption_today",
        "pellets_gestern": "fuel_consumption_yesterday",
    },
}
