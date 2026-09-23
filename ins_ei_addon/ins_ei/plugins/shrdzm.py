"""SHRDZM SMARTMETER Modbus TCP plugin."""
from __future__ import annotations
from .base import DeviceIdentity,PluginPoint,PluginProbe
from .transports.shrdzm_modbus import SHRDZMModbusTransport

class SHRDZMPlugin:
    plugin_id="shrdzm";manufacturer="SHRDZM"
    def __init__(self,host,port=502,unit_id=1,registers=None):
        self.transport=SHRDZMModbusTransport(host,port,unit_id)
    def detect(self):
        return DeviceIdentity("SHRDZM","SMARTMETER",profile="smartmeter_1_3x_modbus",capabilities={"grid_meter","modbus_tcp"})
    def read(self):
        d=self.transport.read_smartmeter()
        points=[
            PluginPoint("grid","power",d["power_w"],"W","modbus:0x0005"),
            PluginPoint("grid","import_energy",d["import_energy_kwh"],"kWh","modbus:0x0007"),
            PluginPoint("grid","export_energy",d["export_energy_kwh"],"kWh","modbus:0x0009"),
            PluginPoint("grid","voltage_l1",d["voltage_l1_v"],"V","modbus:0x000B"),
            PluginPoint("grid","voltage_l2",d["voltage_l2_v"],"V","modbus:0x000D"),
            PluginPoint("grid","voltage_l3",d["voltage_l3_v"],"V","modbus:0x000F"),
            PluginPoint("grid","current_l1",d["current_l1_a"],"A","modbus:0x0011"),
            PluginPoint("grid","current_l2",d["current_l2_a"],"A","modbus:0x0013"),
            PluginPoint("grid","current_l3",d["current_l3_a"],"A","modbus:0x0015"),
        ]
        diagnostics={
            "power_import_w":d["power_import_w"],
            "power_export_w":d["power_export_w"],
            "reactive_power_import_var":d["reactive_power_import_var"],
            "reactive_power_export_var":d["reactive_power_export_var"],
        }
        return PluginProbe(self.detect(),points,diagnostics)
