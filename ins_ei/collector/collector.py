"""Read-only collector that populates an existing SiteModel."""
from dataclasses import dataclass
from ins_ei.adapters.home_assistant import EntityMapping, HomeAssistantAdapter
from ins_ei.model import SiteModel

@dataclass(slots=True)
class CollectionResult:
    read:int=0; good:int=0; unavailable:int=0; stale:int=0; unknown_component:int=0

class Collector:
    def __init__(self, site:SiteModel, adapter:HomeAssistantAdapter)->None: self.site,self.adapter=site,adapter
    def collect(self,mappings:list[EntityMapping])->CollectionResult:
        result=CollectionResult()
        for mapping in mappings:
            result.read+=1; component=self.site.component(mapping.component_id)
            if component is None: result.unknown_component+=1; continue
            point=self.adapter.read(mapping); component.points[mapping.point]=point
            if point.quality.value=="GOOD": result.good+=1
            elif point.quality.value=="STALE": result.stale+=1
            else: result.unavailable+=1
        return result
