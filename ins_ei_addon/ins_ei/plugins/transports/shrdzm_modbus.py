"""Minimal read-only Modbus TCP transport for SHRDZM smart meters."""
from __future__ import annotations
import socket,struct

class SHRDZMModbusTransport:
    def __init__(self,host,port=502,unit_id=1,timeout=5.0):
        self.host=host.strip();self.port=int(port);self.unit_id=int(unit_id);self.timeout=timeout;self._tid=0
    def read_registers(self,address,count):
        self._tid=(self._tid+1)&0xffff or 1
        pdu=struct.pack(">BHH",0x03,int(address),int(count))
        frame=struct.pack(">HHHB",self._tid,0,len(pdu)+1,self.unit_id)+pdu
        with socket.create_connection((self.host,self.port),timeout=self.timeout) as sock:
            sock.sendall(frame);header=self._recv(sock,7);tid,proto,length,unit=struct.unpack(">HHHB",header)
            if tid!=self._tid or proto!=0 or unit!=self.unit_id:raise RuntimeError("SHRDZM_MBAP")
            payload=self._recv(sock,length-1)
        if not payload or payload[0]&0x80:raise RuntimeError("SHRDZM_MODBUS_EXCEPTION")
        if payload[0]!=0x03 or payload[1]!=count*2:raise RuntimeError("SHRDZM_MODBUS_LENGTH")
        return list(struct.unpack(">"+("H"*count),payload[2:]))
    def read_value(self,address,kind="s16",scale=1.0,word_order="big"):
        count=2 if kind in ("u32","s32","f32") else 1
        words=self.read_registers(address,count)
        if count==2 and word_order=="little":words=list(reversed(words))
        if kind=="u16":v=words[0]
        elif kind=="s16":v=struct.unpack(">h",struct.pack(">H",words[0]))[0]
        elif kind=="u32":v=(words[0]<<16)|words[1]
        elif kind=="s32":v=struct.unpack(">i",struct.pack(">HH",*words))[0]
        elif kind=="f32":v=struct.unpack(">f",struct.pack(">HH",*words))[0]
        else:raise ValueError(f"unsupported register type: {kind}")
        return v*scale
    def _recv(self,sock,n):
        data=b""
        while len(data)<n:
            part=sock.recv(n-len(data))
            if not part:raise RuntimeError("SHRDZM_CONNECTION_CLOSED")
            data+=part
        return data
