"""INS-EI Ingress API."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
from urllib.parse import urlparse
from ins_ei.catalog import component_choices,point_choices,point_spec,point_description
from ins_ei.adapters.mapping import _parse_bulk_text,validate_mapping_config
ROOT=Path("/opt/ins-ei/web"); DATA=Path("/data/ui_mappings.json"); OPTIONS=Path("/data/options.json"); DISC=Path("/data/discovery.json"); COMPONENTS=Path("/data/components.json")
def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):return d
def rows():
    value=load(DATA,None)
    return value if value is not None else list(load(OPTIONS,{}).get("mappings",[]))
def save(value):
    # Persist only source identity and explicit conversion overrides.
    # Catalog-owned semantics (unit/role/freshness) must never be frozen in UI data.
    clean=[]
    for item in value:
        row={key:item[key] for key in ("component_id","point","entity_id") if key in item}
        for key in ("scale","invert_sign","max_age_seconds"):
            if key in item and item[key] is not None:row[key]=item[key]
        clean.append(row)
    DATA.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding="utf-8")
def validate(value):return validate_mapping_config({"mappings":value}).errors
class H(BaseHTTPRequestHandler):
    def js(self,obj,status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        p=urlparse(self.path).path
        if p.endswith("/api/catalog"):return self.js({"components":component_choices(),"points":{c:point_choices(c) for c in component_choices()},"meta":{c:{x:{"unit":point_spec(c,x).unit,"role":point_spec(c,x).role,"freshness":point_spec(c,x).freshness,"description":point_description(c,x)} for x in point_choices(c)} for c in component_choices()}})
        if p.endswith("/api/mappings"):return self.js(rows())
        if p.endswith("/api/components"):return self.js(load(COMPONENTS,{}))
        if p.endswith("/api/discovery"):return self.js(load(DISC,[]))
        if p.endswith("/api/entities"):
            items=load(DISC,[])
            return self.js([{"entity_id":x.get("entity_id"),"name":x.get("name"),"state":x.get("state"),"unit":x.get("unit"),"suggested_domain":x.get("suggested_domain"),"suggested_point":x.get("suggested_point")} for x in items])
        raw=(ROOT/"index.html").read_bytes();self.send_response(200);self.send_header("Content-Type","text/html");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        p=urlparse(self.path).path;n=int(self.headers.get("Content-Length","0"));body=json.loads(self.rfile.read(n) or b"{}")
        if p.endswith("/api/components"):
            COMPONENTS.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding="utf-8");return self.js({"saved":True})
        if p.endswith("/api/discovery/accept"):
            x=body["item"];candidate={"component_id":x.get("suggested_domain"),"point":x.get("suggested_point"),"entity_id":x.get("entity_id")};new=rows()+[candidate];err=validate(new)
            if err:return self.js({"errors":err},400)
            save(new);return self.js({"rows":new})
        if p.endswith("/api/import"):
            current=rows();entities={x["entity_id"] for x in current};points={f'{x["component_id"]}.{x["point"]}' for x in current};added=skipped=0
            for x in _parse_bulk_text(body.get("text","")):
                key=f'{x["component_id"]}.{x["point"]}'
                if x["entity_id"] in entities or key in points:skipped+=1;continue
                current.append(x);entities.add(x["entity_id"]);points.add(key);added+=1
            err=validate(current)
            if err:return self.js({"errors":err},400)
            save(current);return self.js({"rows":current,"added":added,"skipped":skipped})
        if p.endswith("/api/mappings"):
            value=body.get("rows",[]);err=validate(value)
            if err:return self.js({"errors":err},400)
            save(value);return self.js({"saved":len(value)})
        return self.js({"error":"not found"},404)
ThreadingHTTPServer(("0.0.0.0",8099),H).serve_forever()
