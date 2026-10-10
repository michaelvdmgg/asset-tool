"""What every discovery adapter returns: one Observation per thing it saw."""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field

# status describes how sure we are of the *instrument's* identity, not that the device exists:
#   confirmed  - manufacturer/model/serial reported electronically by the device itself
#   inferred   - derived from names, vendor IDs or configuration (plausible, not proven)
#   unresolved - something is there, but its physical identity is unknown
STATUSES = ("confirmed", "inferred", "unresolved")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Observation:
    source: str                     # adapter: windows-pnp, visa, revel-channels, ...
    interface_id: str               # stable ID within that source (PnP instance ID, VISA resource, Revel device name)
    status: str
    confidence: str                 # high | medium | low
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None       # the instrument's serial, only when it belongs to the instrument
    hardware_id: str | None = None  # interface-level ID (USB VID:PID + descriptor serial, adapter serial, ...)
    equipment_class: str | None = None
    connection: str | None = None   # usb, serial, visa-tcpip, revel-ethernet, ...
    name: str | None = None         # what the OS / Revel calls it
    revel_device: str | None = None
    revel_channels: list[str] = field(default_factory=list)
    evidence: str = ""              # how the identity was derived
    missing: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=now_iso)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"bad status {self.status!r}")
        self.missing = [f for f in ("manufacturer", "model", "serial") if not getattr(self, f)]

    def to_dict(self):
        return asdict(self)
