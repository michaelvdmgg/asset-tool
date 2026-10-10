"""Windows-local discovery: Plug and Play devices (USB descriptors, COM ports, cameras, printers).

Read-only: asks Windows what it has enumerated (Win32_PnPEntity). No port is opened and nothing
is sent to any device. Hardware plugged into the Revel box is invisible from here.
"""
from __future__ import annotations

import json
import re
import subprocess

from .model import Observation

PS_QUERY = ("Get-CimInstance Win32_PnPEntity | "
            "Select-Object Name,Manufacturer,PNPClass,PNPDeviceID,Service,Status,Present | "
            "ConvertTo-Json -Compress")

# USB vendor IDs worth naming. Generic USB-serial bridge chips are flagged: their descriptor
# serial belongs to the adapter cable, not to the instrument behind it.
VENDORS = {
    "3923": "National Instruments", "04F9": "Brother", "0957": "Keysight", "2A8D": "Keysight",
    "0699": "Tektronix", "1AB1": "Rigol", "05E6": "Keithley", "1313": "Thorlabs",
    "09DB": "Measurement Computing", "0CD5": "LabJack", "16D0": "Alicat (via MCS)",
    "32E4": "ELP / Ailipu", "05A3": "ARC (ELP OEM)", "09CB": "FLIR", "1E4E": "GroupGets / PureThermal",
}
USB_SERIAL_BRIDGES = {"0403": "FTDI", "067B": "Prolific", "10C4": "Silicon Labs", "1A86": "QinHeng (CH340)"}

USB_ID = re.compile(r"^USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(&MI_[0-9A-F]{2})?\\(.+)$", re.I)
FTDI_ID = re.compile(r"^FTDIBUS\\VID_([0-9A-F]{4})\+PID_([0-9A-F]{4})\+([^\\]+)\\", re.I)
COM = re.compile(r"\((COM\d+)\)")
SKIP_SERVICES = {"usbhub", "usbhub3", "usbxhci", "usbehci", "usbuhci", "usbohci"}


def run_query():
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    data = json.loads(out or "[]")
    return [data] if isinstance(data, dict) else data


def trustworthy_serial(s):
    """Windows makes up instance IDs containing '&'; some devices report placeholder serials."""
    if not s or "&" in s:
        return False
    return len(s) >= 4 and not re.fullmatch(r"0*1?|(.)\1+|0123456789(ABCDEF)?", s, re.I)


def observe(devices):
    """Turn Win32_PnPEntity rows into observations: one per physical USB device, plus COM ports
    and printers that aren't on USB. Child interfaces (MI_xx) only lend their friendly names."""
    parents, children, other = {}, {}, []
    for d in devices:
        if d.get("Present") is False:
            continue
        pid_str = d.get("PNPDeviceID") or ""
        m = USB_ID.match(pid_str)
        if m:
            vid, pid, iface, inst = m.group(1).upper(), m.group(2).upper(), m.group(3), m.group(4)
            if (d.get("Service") or "").lower() in SKIP_SERVICES:
                continue
            (children.setdefault((vid, pid), []) if iface else parents.setdefault((vid, pid), [])).append((d, inst))
        elif pid_str.upper().startswith("FTDIBUS\\") or d.get("PNPClass") in ("Ports", "Printer"):
            other.append(d)

    obs = []
    for (vid, pid), items in parents.items():
        names = [c.get("Name") for c, _ in children.get((vid, pid), []) if c.get("Name")]
        for d, inst in items:
            obs.append(_usb_obs(d, vid, pid, inst, names if len(items) == 1 else []))
    # interfaces of a composite device whose parent we didn't see (rare): still report them
    for (vid, pid), items in children.items():
        if (vid, pid) not in parents:
            for d, _ in items:
                obs.append(_usb_obs(d, vid, pid, None, []))
    for d in other:
        obs.append(_other_obs(d))
    return obs


def _usb_obs(d, vid, pid, inst, child_names):
    name = d.get("Name") or ""
    if name == "USB Composite Device" and child_names:
        name = child_names[0]
    port = next((COM.search(n).group(1) for n in [name, *child_names] if COM.search(n)), None)
    serial_ok = trustworthy_serial(inst)
    bridge = USB_SERIAL_BRIDGES.get(vid)
    vendor = VENDORS.get(vid) or bridge or d.get("Manufacturer")
    hw = f"usb:{vid}:{pid}" + (f":{inst}" if serial_ok else "")
    pnp_class = d.get("PNPClass") or ""
    cls = ("usb-serial adapter" if bridge else "camera" if pnp_class in ("Camera", "Image") or "camera" in name.lower()
           else "label printer" if vid == "04F9" else None)
    if bridge:
        status, conf = "unresolved", "low"
        ev = (f"{bridge} USB-serial bridge; descriptor serial {inst if serial_ok else '(none)'} identifies the "
              "adapter, not the instrument behind it")
        serial = None
    elif serial_ok:
        status, conf = ("confirmed", "high" if vid in VENDORS else "medium")
        ev = "USB device descriptor serial number reported by the device"
        serial = inst
    else:
        status, conf = "unresolved", "low"
        ev = "USB device without a usable descriptor serial (Windows-generated instance ID)"
        serial = None
    return Observation(source="windows-pnp", interface_id=d.get("PNPDeviceID"), status=status, confidence=conf,
                       manufacturer=vendor, model=f"USB {vid}:{pid}", serial=serial, hardware_id=hw,
                       equipment_class=cls, connection=f"usb{'/' + port if port else ''}", name=name,
                       evidence=ev)


def _other_obs(d):
    pid_str = d.get("PNPDeviceID") or ""
    name = d.get("Name") or ""
    port = COM.search(name)
    m = FTDI_ID.match(pid_str)
    hw = f"ftdi:{m.group(1)}:{m.group(2)}:{m.group(3)}" if m else pid_str
    ev = ("FTDI virtual COM port; serial identifies the adapter" if m else
          "Windows device; no electronically reported instrument identity")
    return Observation(source="windows-pnp", interface_id=pid_str, status="unresolved", confidence="low",
                       manufacturer=d.get("Manufacturer"), hardware_id=hw,
                       equipment_class="serial port" if port else (d.get("PNPClass") or "").lower() or None,
                       connection=port.group(1) if port else None, name=name, evidence=ev)


def discover():
    return observe(run_query())
