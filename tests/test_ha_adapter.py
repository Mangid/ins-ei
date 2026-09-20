from ins_ei.adapters.home_assistant import EntityMapping, HomeAssistantAdapter
from ins_ei.model import DataQuality, DataRole
class FakeClient:
    def state(self,entity_id): return {"state":"-1250","last_updated":None,"attributes":{"unit_of_measurement":"W"}}
def test_numeric_mapping_and_sign_inversion():
    point=HomeAssistantAdapter(FakeClient()).read(EntityMapping(component_id="grid",point="power",entity_id="sensor.grid",role=DataRole.OPTIMIZATION,invert_sign=True))
    assert point.value==1250 and point.unit=="W" and point.quality==DataQuality.GOOD
