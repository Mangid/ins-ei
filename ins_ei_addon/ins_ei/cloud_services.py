"""Market, weather and forecast are cloud data services, not device plugins."""
from datetime import datetime,timezone
import json
class CloudDataCache:
    def __init__(self,path):self.path=path
    def read_all(self):
        try:return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:return {}
    def write(self,service,payload):
        d=self.read_all();d[service]={"updated_at":datetime.now(timezone.utc).isoformat(),"data":payload}
        self.path.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
    def get(self,service):return self.read_all().get(service)
