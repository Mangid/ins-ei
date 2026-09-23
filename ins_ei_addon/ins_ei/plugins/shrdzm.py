"""SHRDZM Smartmeter plugin using Modbus TCP as the primary transport.

The register map is configurable because SHRDZM firmware/device profiles can
differ. No HTTP credentials are required for normal operation.
"""
from __future__ import annotations
from .base import DeviceIdentity,PluginPoint,PluginProbe
from .transports.shrdzm_modbus import SHRDZMModbusTransport

class SHRDZMPlugin:
    plugin_id="shrdzm";manufacturer="SHRDZM"
    def __init__(self,host,port=502,unit_id=1,registers=None):
        self.transport=SHRDZMModbusTransport(host,port,unit_id)
        self.registers=registers or {}
    def detect(self):
        return DeviceIdentity("SHRDZM","SMARTMETER",profile="modbus_tcp",capabilities={"grid_meter","modbus_tcp"})
    def read(self):
        points=[];unmapped={}
        specs={
          "power":("grid","power","W"),
          "import_energy":("grid","import_energy","kWh"),
          "export_energy":("grid","export_energy","kWh"),
          "frequency":("grid","frequency","Hz"),
          "voltage_l1":("grid","voltage_l1","V"),
          "voltage_l2":("grid","voltage_l2","V"),
          "voltage_l3":("grid","voltage_l3","V"),
          "current_l1":("grid","current_l1","A"),
          "current_l2":("grid","current_l2","A"),
          "current_l3":("grid","current_l3","A"),
        }
        for name,cfg in self.registers.items():
            if name not in specs or not isinstance(cfg,dict) or "address" not in cfg:continue
            try:
                value=self.transport.read_value(
                    int(cfg["address"]),str(cfg.get("type","s16")),
                    float(cfg.get("scale",1.0)),str(cfg.get("word_order","big"))
                )
                if cfg.get("invert_sign"):value=-value
                cid,point,unit=specs[name]
                points.append(PluginPoint(cid,point,value,unit,f"modbus:{cfg['address']}"))
            except Exception as exc:unmapped[f"error.{name}"]=str(exc)
        return PluginProbe(self.detect(),points,unmapped)
