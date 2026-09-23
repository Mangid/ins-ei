"""Read-only Modbus TCP transport for SHRDZM smart meters."""
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
    # SHRDZM 1.3.x exposes 32-bit values low-word first.
    @staticmethod
    def u32(words,index):return (words[index+1]<<16)|words[index]
    @staticmethod
    def s32(words,index):return struct.unpack(">i",struct.pack(">HH",words[index+1],words[index]))[0]
    def read_smartmeter(self):
        # SHRDZM SMARTMETER firmware 1.3.x: FC03, registers 0x0000-0x001A.
        r=self.read_registers(0x0000,27)
        if r[0]==0:raise RuntimeError("SHRDZM_DATA_INVALID")
        return {
            "valid":r[0],
            "power_import_w":self.u32(r,1),
            "power_export_w":self.u32(r,3),
            "power_w":self.s32(r,5),
            "import_energy_kwh":self.u32(r,7)/1000.0,
            "export_energy_kwh":self.u32(r,9)/1000.0,
            "voltage_l1_v":self.s32(r,11)/1000.0,
            "voltage_l2_v":self.s32(r,13)/1000.0,
            "voltage_l3_v":self.s32(r,15)/1000.0,
            "current_l1_a":self.s32(r,17)/1000.0,
            "current_l2_a":self.s32(r,19)/1000.0,
            "current_l3_a":self.s32(r,21)/1000.0,
            "reactive_power_import_var":self.u32(r,23),
            "reactive_power_export_var":self.u32(r,25),
        }
    def _recv(self,sock,n):
        data=b""
        while len(data)<n:
            part=sock.recv(n-len(data))
            if not part:raise RuntimeError("SHRDZM_CONNECTION_CLOSED")
            data+=part
        return data
