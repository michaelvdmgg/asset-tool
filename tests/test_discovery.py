import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from discovery import reconcile, revel, windows  # noqa: E402
from discovery.model import Observation  # noqa: E402

PNP = [
    {"Name": "USB Composite Device", "PNPClass": "USB", "Service": "usbccgp", "Manufacturer": "(Standard)",
     "PNPDeviceID": "USB\\VID_32E4&PID_9230\\SN0012345", "Present": True},
    {"Name": "HD USB Camera", "PNPClass": "Camera", "Service": "usbvideo", "Manufacturer": "Microsoft",
     "PNPDeviceID": "USB\\VID_32E4&PID_9230&MI_00\\7&ABC&0&0000", "Present": True},
    {"Name": "USB Serial Converter", "PNPClass": "USB", "Service": "FTDIBUS", "Manufacturer": "FTDI",
     "PNPDeviceID": "USB\\VID_0403&PID_6001\\A9XYZ123", "Present": True},
    {"Name": "USB Serial Port (COM5)", "PNPClass": "Ports", "Service": "FTSER2K", "Manufacturer": "FTDI",
     "PNPDeviceID": "FTDIBUS\\VID_0403+PID_6001+A9XYZ123A\\0000", "Present": True},
    {"Name": "Integrated Camera", "PNPClass": "USB", "Service": "usbccgp", "Manufacturer": "(Standard)",
     "PNPDeviceID": "USB\\VID_04F2&PID_B828\\0001", "Present": True},
    {"Name": "USB Root Hub (USB 3.0)", "PNPClass": "USB", "Service": "USBHUB3", "Manufacturer": "(Standard)",
     "PNPDeviceID": "USB\\ROOT_HUB30\\5&6FBED17&0&0", "Present": True},
    {"Name": "Unplugged thing", "PNPClass": "USB", "Service": "usbccgp",
     "PNPDeviceID": "USB\\VID_1234&PID_5678\\XYZ999", "Present": False},
]


def by_hw(obs):
    return {o.hardware_id: o for o in obs}


def test_windows_usb_identity_rules():
    obs = by_hw(windows.observe(PNP))
    cam = obs["usb:32E4:9230:SN0012345"]
    assert cam.status == "confirmed" and cam.serial == "SN0012345" and cam.name == "HD USB Camera"
    ftdi = obs["usb:0403:6001:A9XYZ123"]
    assert ftdi.status == "unresolved" and ftdi.serial is None  # adapter serial is not the instrument's
    assert obs["usb:04F2:B828"].status == "unresolved"            # placeholder serial "0001"
    assert not any("VID_1234" in o.interface_id for o in obs.values())  # not present
    assert not any("ROOT_HUB" in o.interface_id for o in obs.values())


def test_revel_ni_chassis_and_module_names():
    body = {"timestamp": 1, "values": {
        "cDAQ9189_2A23393_ai_high_speed_50000_hz.enabled": True,
        "cDAQ9189_2A23393Mod3_ai0": 4.0, "pt1": 1.0, "meta.sn": 3, "alicat_1.flow": 2.0}}
    obs, notes = revel.observe(body)
    chassis = next(o for o in obs if o.equipment_class == "DAQ chassis")
    assert (chassis.model, chassis.serial, chassis.status) == ("cDAQ-9189", "2A23393", "inferred")
    mod = next(o for o in obs if o.equipment_class == "DAQ module")
    assert mod.hardware_id == "ni-chassis:cDAQ9189-2A23393/slot3" and mod.serial is None
    assert notes["unmapped_channels"] == ["alicat_1.flow", "meta.sn", "pt1"]
    assert notes["family_hints"] == {"Alicat": ["alicat_1.flow"]}


def test_merge_flags_conflicting_models_and_inventory_matching():
    a = Observation("x", "1", "confirmed", "high", manufacturer="NI", model="A", serial="S1", hardware_id="h1")
    b = Observation("y", "2", "inferred", "medium", manufacturer="NI", model="B", serial="S1", hardware_id="h2")
    entries, conflicts = reconcile.merge([a, b])
    assert len(entries) == 1 and entries[0]["sources"] == ["x", "y"] and conflicts

    c = Observation("x", "3", "unresolved", "low", hardware_id="usb:32E4:9230:Q", name="HD USB Camera")
    d = Observation("x", "4", "unresolved", "low", hardware_id="usb:32E4:9230:R", name="HD USB Camera")
    entries, _ = reconcile.merge([c, d])
    rows, extra = reconcile.match_inventory(entries, [{"name": "cam", "match": "hw:usb:32E4"}])
    assert rows[0]["entry"] is None and rows[0]["match"].startswith("ambiguous")  # never guess
    assert len(extra) == 2
