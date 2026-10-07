from __future__ import annotations
import json
from urllib.request import Request, urlopen

class MyPvAcThor9s:
    def __init__(self, config: dict):
        self.name=config.get("name","my-PV AC-THOR 9s")
        self.url=config["url"]
        self.timeout=float(config.get("timeout_seconds",3))

    def read(self) -> tuple[dict,dict]:
        req=Request(self.url,headers={"Accept":"application/json","User-Agent":"INS-EI-Gateway/0.1"})
        with urlopen(req,timeout=self.timeout) as response:
            raw=json.loads(response.read().decode("utf-8"))
        values={
            "p2h.power_w": self._number(raw.get("power_ac9")),
            "p2h.power1_w": self._number(raw.get("power1_solar")),
            "p2h.power2_w": self._number(raw.get("power2_solar")),
            "p2h.power3_w": self._number(raw.get("power3_solar")),
            "p2h.nominal_power_w": self._number(raw.get("power_nominal")),
        }
        values={k:v for k,v in values.items() if v is not None}
        diagnostics={
            "plugin":"mypv","adapter":"ac_thor_9s","name":self.name,
            "device":raw.get("device"),"variant":raw.get("acthor9s"),
            "firmware":raw.get("fwversion"),"error_state":raw.get("error_state"),
            "control_state":raw.get("ctrlstate"),
        }
        return values,diagnostics

    @staticmethod
    def _number(value):
        if value is None or isinstance(value,bool): return None
        try:return float(value)
        except (TypeError,ValueError):return None
