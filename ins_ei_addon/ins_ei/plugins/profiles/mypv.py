"""Known my-PV profiles from existing INS installations."""

AC_THOR = {
    "family": "AC-THOR",
    "capabilities": {"power_to_heat"},
    "protocols": {"http_data_jsn", "modbus_tcp"},
}

AC_THOR_9S = {
    "family": "AC-THOR 9s",
    "capabilities": {"power_to_heat", "three_phase", "0_9kw"},
    "protocols": {"http_data_jsn", "modbus_tcp"},
    # Existing INS interface reads Modbus TCP unit 1 registers 1057-1084
    # plus HTTP /data.jsn. Control registers remain separate from telemetry.
    "modbus_unit": 1,
    "telemetry_register_range": (1057, 1084),
}

AC_ELWA_2 = {
    "family": "AC ELWA 2",
    "capabilities": {"power_to_heat"},
    "protocols": {"http_data_jsn", "modbus_tcp"},
}
