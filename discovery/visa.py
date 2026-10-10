"""VISA instruments (USB-TMC, LXI/TCPIP, GPIB) through PyVISA, if it and a VISA backend are installed.

Listing resources is passive. The *IDN? identification query (IEEE 488.2; changes no settings) is
only sent when asked for (--visa-idn), and never to serial (ASRL) resources, whose protocol is
unknown.
"""
from __future__ import annotations

from .model import Observation


def discover(idn=False, timeout_ms=2000):
    try:
        import pyvisa
    except ImportError:
        return [], "pyvisa not installed (py -m pip install pyvisa pyvisa-py) - VISA skipped"
    try:
        rm = pyvisa.ResourceManager()
        resources = rm.list_resources()
    except Exception as e:  # no backend, etc.
        return [], f"VISA unavailable: {e}"
    obs = []
    for res in resources:
        kind = res.split(":")[0].rstrip("0123456789").lower()
        if kind == "asrl":
            continue  # serial ports are covered by windows-pnp; don't probe them
        o = Observation(source="visa", interface_id=res, status="unresolved", confidence="low",
                        hardware_id=res, connection=f"visa-{kind}", evidence="VISA resource listed; not queried")
        if idn:
            try:
                inst = rm.open_resource(res, timeout=timeout_ms)
                reply = inst.query("*IDN?").strip()
                inst.close()
                parts = [p.strip() for p in reply.split(",")]
                if len(parts) >= 3 and parts[2] and parts[2] not in ("0", "N/A"):
                    o = Observation(source="visa", interface_id=res, status="confirmed", confidence="high",
                                    manufacturer=parts[0], model=parts[1], serial=parts[2], hardware_id=res,
                                    connection=f"visa-{kind}", name=reply,
                                    evidence="*IDN? reply from the instrument")
                else:
                    o.evidence, o.name = "*IDN? reply without a serial", reply
            except Exception as e:
                o.evidence = f"*IDN? failed: {e}"
        obs.append(o)
    return obs, None
