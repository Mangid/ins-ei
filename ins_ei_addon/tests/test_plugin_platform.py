from pathlib import Path
from ins_ei.plugin_manager import PluginManager
from ins_ei.plugin_sdk import PluginManifest,PluginKind,ActuatorDescriptor
from ins_ei.actuator_manager_v2 import ActuatorManager

class Fake:
    def __init__(self,iid,cfg):self.instance_id=iid;self.values={"switch":False}
    def manifest(self):return PluginManifest("fake","Fake","1",PluginKind.DEVICE)
    def inputs(self):return []
    def actuators(self):return [ActuatorDescriptor("switch","Switch","test.switch")]
    def read_points(self):return {}
    def read_actuator(self,a):return self.values[a]
    def write_actuator(self,a,v):self.values[a]=v
    def verify_actuator(self,a,v):return self.values[a]==v
    def health(self):return None

def test_baseline_apply_restore(tmp_path:Path):
    pm=PluginManager();pm.register(PluginManifest("fake","Fake","1",PluginKind.DEVICE),lambda i,c:Fake(i,c))
    p=pm.create("fake","fake.0",{})
    am=ActuatorManager(pm,tmp_path/"actuators.json");am.commission()
    assert am.state["baseline"]["fake.0:switch"]["value"] is False
    am.apply("fake.0","switch",True,"optimizer");assert p.values["switch"] is True
    assert not am.restore_all();assert p.values["switch"] is False
    assert am.state["owned"]=={}
