"""Local INS-EI Ingress mapping UI server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
from urllib.parse import urlparse

from ins_ei.catalog import component_choices, point_choices
from ins_ei.adapters.mapping import _parse_bulk_text, validate_mapping_config

ROOT = Path("/opt/ins-ei/web")
DATA = Path("/data/ui_mappings.json")
OPTIONS = Path("/data/options.json")

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def load_rows():
    rows = load_json(DATA, None)
    if rows is not None:
        return rows
    options = load_json(OPTIONS, {})
    return list(options.get("mappings", []))

def save_rows(rows):
    DATA.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

def validate_rows(rows):
    result = validate_mapping_config({"mappings": rows})
    return result.errors

class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.endswith("/api/catalog"):
            return self._json({"components": component_choices(), "points": {c: point_choices(c) for c in component_choices()}})
        if path.endswith("/api/mappings"):
            return self._json(load_rows())
        raw = (ROOT / "index.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if path.endswith("/api/import"):
            rows = load_rows()
            existing_entities = {r["entity_id"] for r in rows}
            existing_points = {f'{r["component_id"]}.{r["point"]}' for r in rows}
            added, skipped = [], []
            for item in _parse_bulk_text(body.get("text", "")):
                logical = f'{item["component_id"]}.{item["point"]}'
                if item["entity_id"] in existing_entities or logical in existing_points:
                    skipped.append(item)
                    continue
                rows.append(item)
                added.append(item)
                existing_entities.add(item["entity_id"])
                existing_points.add(logical)
            errors = validate_rows(rows)
            if errors:
                return self._json({"errors": errors, "rows": load_rows()}, 400)
            save_rows(rows)
            return self._json({"added": len(added), "skipped": len(skipped), "rows": rows})
        if path.endswith("/api/mappings"):
            rows = body.get("rows", [])
            errors = validate_rows(rows)
            if errors:
                return self._json({"errors": errors}, 400)
            save_rows(rows)
            return self._json({"saved": len(rows), "restart_required": False})
        return self._json({"error": "not found"}, 404)

ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
