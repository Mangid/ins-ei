"""Read-only OekoFEN JSON /all transport, migrated from eo_oekofen."""
from __future__ import annotations
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RATE_LIMIT_TEXT="wait at least 2500ms"

class OekoFENTransport:
    def __init__(self,host:str,password:str,port:int=4321,timeout:float=10.0):
        self.host=host.strip();self.password=password;self.port=int(port);self.timeout=timeout
    def read_all(self)->dict:
        url=f"http://{self.host}:{self.port}/{self.password}/all"
        req=Request(url,headers={"Accept":"application/json","Connection":"close"})
        try:
            with urlopen(req,timeout=self.timeout) as response:
                body=response.read();status=response.status
        except HTTPError as exc:
            body=exc.read();status=exc.code
        except (URLError,TimeoutError,OSError) as exc:
            raise RuntimeError(f"OEKOFEN_HTTP:{type(exc).__name__}") from exc
        text=None
        for enc in ("utf-8","cp1252","latin-1"):
            try:text=body.decode(enc);break
            except UnicodeDecodeError:pass
        text=text or ""
        if RATE_LIMIT_TEXT in text.strip().lower():raise RuntimeError("OEKOFEN_REQUEST_ABSTAND")
        if status!=200:raise RuntimeError(f"OEKOFEN_HTTP_{status}")
        try:data=json.loads(text)
        except json.JSONDecodeError as exc:raise RuntimeError("OEKOFEN_JSON_UNGUELTIG") from exc
        if not isinstance(data,dict):raise RuntimeError("OEKOFEN_JSON_KEIN_OBJECT")
        return data
