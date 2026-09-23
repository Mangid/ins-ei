"""Read-only my-PV local transport migrated from ins_mypv.

Supports the proven AC-THOR 9s telemetry path: Modbus TCP FC03, unit 1,
registers 1057-1084, plus optional HTTP /data.jsn.
"""
from __future__ import annotations
import json,socket,struct
from urllib.request import urlopen

REG_START=1057;REG_END=1084;REG_COUNT=REG_END-REG_START+1

def _s16(value:int)->int:return value-65536 if value>=32768 else value

class MyPVTransport:
    def __init__(self,host:str,port:int=502,unit_id:int=1,timeout:float=5.0,http_enabled:bool=True):
        self.host=host.strip();self.port=int(port);self.unit_id=int(unit_id);self.timeout=timeout;self.http_enabled=http_enabled
    def read_modbus(self)->dict[int,int]:
        tid=1
        pdu=struct.pack(">BHH",0x03,REG_START,REG_COUNT)
        frame=struct.pack(">HHHB",tid,0,len(pdu)+1,self.unit_id)+pdu
        with socket.create_connection((self.host,self.port),timeout=self.timeout) as sock:
            sock.sendall(frame);header=self._recv(sock,7);r_tid,proto,length,r_unit=struct.unpack(">HHHB",header)
            if r_tid!=tid or proto!=0 or r_unit!=self.unit_id:raise RuntimeError("MYPV_MBAP")
            payload=self._recv(sock,length-1)
        if not payload or payload[0]&0x80:raise RuntimeError("MYPV_MODBUS_EXCEPTION")
        if payload[0]!=0x03 or payload[1]!=REG_COUNT*2:raise RuntimeError("MYPV_MODBUS_LENGTH")
        words=struct.unpack(">"+("H"*REG_COUNT),payload[2:])
        return {REG_START+i:int(v) for i,v in enumerate(words)}
    def _recv(self,sock,n):
        data=b""
        while len(data)<n:
            part=sock.recv(n-len(data))
            if not part:raise RuntimeError("MYPV_CONNECTION_CLOSED")
            data+=part
        return data
    def read_http(self)->dict:
        if not self.http_enabled:return {}
        with urlopen(f"http://{self.host}/data.jsn",timeout=self.timeout) as r:
            if r.status!=200:raise RuntimeError(f"MYPV_HTTP_{r.status}")
            data=json.loads(r.read().decode("utf-8",errors="replace"))
        if not isinstance(data,dict):raise RuntimeError("MYPV_HTTP_JSON")
        return data
    def read(self)->dict:
        m=self.read_modbus();reg=lambda n:m.get(n)
        return {
          "stratification_flag":reg(1057),"relay1_status":reg(1058),"load_state":reg(1059),"load_nominal_power_w":reg(1060),
          "voltage_l1_v":reg(1061),"current_l1_a":reg(1062)/10 if reg(1062)!=None else None,"voltage_out_v":reg(1063),
          "frequency_hz":reg(1064)/1000 if reg(1064)!=None else None,"operation_mode":reg(1065),"state_9s":reg(1066),
          "voltage_l2_v":reg(1067),"current_l2_a":reg(1068)/10 if reg(1068)!=None else None,
          "meter_power_w":_s16(reg(1069)) if reg(1069)!=None else None,"control_type":reg(1070),"pmax_possible_w":reg(1071),
          "voltage_l3_v":reg(1072),"current_l3_a":reg(1073)/10 if reg(1073)!=None else None,
          "power_out1_w":reg(1074),"power_out2_w":reg(1075),"power_out3_w":reg(1076),"operation_state":reg(1077),
          "device_state":reg(1081),"device_power_w":reg(1082),"solar_power_w":reg(1083),"grid_power_w":reg(1084),
          "http":self.read_http() if self.http_enabled else {},
        }
