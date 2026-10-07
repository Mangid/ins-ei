from __future__ import annotations
import json, os, signal, ssl, time
from datetime import datetime, timezone
from pathlib import Path
import yaml
import paho.mqtt.client as mqtt
from .plugins.mypv import MyPvAcThor9s

CONFIG=Path(os.getenv("INS_EI_CONFIG","/config/gateway.yaml"))

def now_iso(): return datetime.now(timezone.utc).isoformat()

def read_secret(env,default):
    path=Path(os.getenv(env,default))
    return path.read_text().strip()

def envelope(site_id,version,sequence,payload):
    return {"api_version":"ins-ei.bus/v1","site_id":site_id,"generated_at":now_iso(),
            "sequence":sequence,"core_version":version,"payload":payload}

def main():
    cfg=yaml.safe_load(CONFIG.read_text())
    site=cfg["site_id"];version=cfg.get("core_version","gateway-dev")
    user=read_secret("MQTT_USERNAME_FILE","/config/mqtt_username")
    password=read_secret("MQTT_PASSWORD_FILE","/config/mqtt_password")
    plugins=[]
    for p in cfg.get("plugins",[]):
        if p.get("type")=="mypv" and p.get("adapter")=="ac_thor_9s":
            plugins.append(MyPvAcThor9s(p))
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=site,clean_session=True)
    client.username_pw_set(user,password);client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    seq=0
    def publish(kind,payload,qos=0,retain=False):
        nonlocal seq;seq+=1
        body=json.dumps(envelope(site,version,seq,payload),separators=(",",":"),ensure_ascii=False)
        info=client.publish(f"ins-ei/{site}/{kind}",body,qos=qos,retain=retain)
        return info
    client.will_set(f"ins-ei/{site}/status",
        json.dumps(envelope(site,version,0,{"online":False,"reason":"connection_lost"}),separators=(",",":")),
        qos=1,retain=True)
    client.connect(cfg["mqtt"]["host"],int(cfg["mqtt"].get("port",8883)),int(cfg["mqtt"].get("keepalive",30)))
    client.loop_start()
    stop=False
    def shutdown(*_):
        nonlocal stop;stop=True
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    publish("status",{"online":True,"gateway":True,"plugins":[p.name for p in plugins]},qos=1,retain=True)
    last_status=0.0
    state_interval=float(cfg.get("state_interval_seconds",5))
    status_interval=float(cfg.get("status_interval_seconds",60))
    try:
        while not stop:
            started=time.monotonic();values={};health=[]
            for plugin in plugins:
                try:
                    v,d=plugin.read();values.update(v);health.append({**d,"online":True})
                except Exception as exc:
                    health.append({"name":plugin.name,"online":False,"error":str(exc)[:300]})
            publish("state",{"values":values},qos=0,retain=False)
            if time.monotonic()-last_status>=status_interval:
                publish("health",{"online":True,"plugins":health},qos=1,retain=False);last_status=time.monotonic()
            time.sleep(max(0.2,state_interval-(time.monotonic()-started)))
    finally:
        try:publish("status",{"online":False,"gateway":True,"reason":"shutdown"},qos=1,retain=True).wait_for_publish(2)
        except Exception:pass
        client.loop_stop();client.disconnect()

if __name__=="__main__":main()
