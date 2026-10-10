"""Revel: what can be learned from the documented Channels API (GET latest_values), read-only.

Revel has no documented hardware-inventory API (question open with Revel), so this adapter only
reads channel *names*. Some carry device identity by convention:

  cDAQ9189_2A23393_...      NI-DAQmx names an Ethernet chassis cDAQ<model>-<serial>; Revel shows
                            it with '_' -> inferred chassis model + serial (check the chassis label)
  cDAQ9189_2A23393Mod3_...  module in slot 3 of that chassis -> slot known, module model/serial not

Everything else (pt1, N2_MFM, meta.*) is a user-named channel: reported, not mapped to hardware.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from .model import Observation

NI_CHASSIS = re.compile(r"^cDAQ(\d{4})[_-]([0-9A-F]{6,8})(?:Mod(\d+))?(?=[_.]|$)", re.I)
HINTS = {  # name fragments that suggest a device family; never treated as identity
    "ethercat": "EtherCAT", "alicat": "Alicat", "labjack": "LabJack", "modbus": "Modbus",
}


def fetch(url, verify=False, timeout=15):
    import requests
    import urllib3
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    r = requests.get(f"{url.rstrip('/')}/api/channels/v1/latest_values", verify=verify, timeout=timeout)
    r.raise_for_status()
    return r.json()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def observe(body, source_url=""):
    """body: the latest_values JSON ({"timestamp": ..., "values": {channel: value}})."""
    names = sorted(body.get("values", body) if isinstance(body, dict) else body)
    chassis, modules, unmapped, hints = defaultdict(list), defaultdict(list), [], defaultdict(list)
    for n in names:
        m = NI_CHASSIS.match(n)
        if m:
            key = (m.group(1), m.group(2).upper())
            (modules[(*key, int(m.group(3)))] if m.group(3) else chassis[key]).append(n)
            continue
        unmapped.append(n)
        for frag, family in HINTS.items():
            if frag in n.lower():
                hints[family].append(n)

    obs = []
    for model, sn in sorted({*chassis, *((m, s) for m, s, _ in modules)}):
        obs.append(Observation(
            source="revel-channels", interface_id=f"cDAQ{model}_{sn}", status="inferred", confidence="medium",
            manufacturer="National Instruments", model=f"cDAQ-{model}", serial=sn,
            hardware_id=f"ni-chassis:cDAQ{model}-{sn}", equipment_class="DAQ chassis",
            connection="revel-ethernet", name=f"cDAQ{model}-{sn}", revel_device=f"cDAQ{model}_{sn}",
            revel_channels=chassis.get((model, sn), []),
            evidence=f"Revel channel names follow NI-DAQmx chassis naming cDAQ<model>-<serial> ({source_url}); "
                     "confirm against the chassis label"))
    for (model, sn, slot), chans in sorted(modules.items()):
        obs.append(Observation(
            source="revel-channels", interface_id=f"cDAQ{model}_{sn}Mod{slot}", status="unresolved",
            confidence="low", hardware_id=f"ni-chassis:cDAQ{model}-{sn}/slot{slot}", equipment_class="DAQ module",
            connection=f"cDAQ-{model} {sn} slot {slot}", name=f"cDAQ{model}-{sn}Mod{slot}",
            revel_device=f"cDAQ{model}_{sn}Mod{slot}", revel_channels=chans,
            evidence="slot known from the channel name; module model and serial not exposed"))
    notes = {"channels_total": len(names), "unmapped_channels": unmapped,
             "family_hints": {k: v for k, v in hints.items()}}
    return obs, notes


def discover(url=None, path=None, verify=False):
    if path:
        return observe(load(path), str(path))
    return observe(fetch(url, verify), url)
