"""GT equipment registry in Jira (base Jira, no Assets add-on).

Every tracked item, racks included, is an "Equipment" issue with a permanent Equipment ID
(e.g. GG-EQ-000012) that goes on its QR label and into Sift. Each calibration is a
"Calibration Event" sub-item under its Equipment issue, with the certificate attached.

  py asset_tool.py setup                       check access, work types and fields
  py asset_tool.py add-rack "GT Test Rack"     register a rack (an Equipment issue of class Test Rack)
  py asset_tool.py add                         register an instrument (prompts for anything missing)
  py asset_tool.py import assets.csv           register many (columns = the add flags)
  py asset_tool.py cal GG-EQ-000003 --date 2026-09-15 --result Pass --cert cert.pdf
  py asset_tool.py check GG-EQ-000003 [--at 2026-10-09]   calibration status on a date
  py asset_tool.py list [--rack GG-EQ-000001]  equipment with status and due date
  py asset_tool.py label GG-EQ-000003          remake / reprint a label

Add --dry-run to use a local fake Jira (dryrun_jira.json) instead of the real one.
"""
import argparse
import csv
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

import calibration as cal
import labels
from jira_client import connect

HERE = Path(__file__).resolve().parent
EQUIPMENT, EVENT = "Equipment", "Calibration Event"
EQ_FIELDS = ["Equipment ID", "Equipment Class", "Manufacturer", "Model", "Serial Number", "Location",
             "Calibration Status", "Last Calibrated"]
EV_FIELDS = ["Calibration Date", "Result", "Provider"]
CLASSES = ["Test Rack", "Pressure Transducer", "Load Cell", "Load Cell Amplifier", "Flow Controller",
           "DAQ Chassis", "DAQ Module", "Thermocouple", "Power Supply", "Other"]
RESULTS = ["Pass", "Fail", "Conditional"]


def load_env(path=HERE / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def tag(prefix, value):
    return f"{prefix}-" + re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").lower()


def today():
    return dt.date.today().isoformat()


def conditional_policy():
    return cal.VALID if os.environ.get("CONDITIONAL_COUNTS_AS", "unknown").lower() == "valid" else cal.UNKNOWN


def ask(prompt, default=None, choices=None):
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i:2d}. {c}")
    while True:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or (default or "")
        if choices:
            if v.isdigit() and 1 <= int(v) <= len(choices):
                return choices[int(v) - 1]
            hit = [c for c in choices if v and c.lower().startswith(v.lower())]
            if len(hit) == 1:
                return hit[0]
            print("  pick a number from the list")
            continue
        if v:
            return v


# --- lookups -------------------------------------------------------------------------------
def find_equipment(jira, ref):
    """ref: Jira key (LAB-12) or Equipment ID (GG-EQ-000012). Returns issue dict or None."""
    names = ["summary", "labels", "duedate"] + EQ_FIELDS
    jql = (f'project = "{jira.project}" AND key = "{ref}"' if re.fullmatch(r"[A-Z]+-\d+", ref) and "-EQ-" not in ref
           else f'project = "{jira.project}" AND labels = "{tag("eqid", ref)}"')
    found = jira.search(jql, names)
    return found[0] if found else None


def must_equipment(jira, ref, tries=6):
    # Jira search lags a few seconds behind issue creation, so a just-created item may not be found yet
    for i in range(tries):
        e = find_equipment(jira, ref)
        if e:
            return e
        if i < tries - 1:
            time.sleep(1.5)
    raise SystemExit(f"No equipment '{ref}' in {jira.project}")


def find_by_serial(jira, sn):
    return jira.search(f'project = "{jira.project}" AND labels = "{tag("sn", sn)}"', ["summary"] + EQ_FIELDS)


def next_equipment_id(jira):
    prefix = os.environ.get("EQUIP_PREFIX", "GG-EQ")
    nums = [int(m.group(1)) for i in jira.search(f'project = "{jira.project}" AND labels = "equipment"', ["Equipment ID"])
            if (m := re.search(r"(\d+)$", i["fields"].get("Equipment ID") or ""))]
    return f"{prefix}-{max(nums, default=0) + 1:06d}"


def events_of(jira, key):
    rows = jira.search(f'project = "{jira.project}" AND parent = "{key}" AND issuetype = "{EVENT}"',
                       ["summary", "duedate"] + EV_FIELDS)
    return [{"key": r["key"], "date": r["fields"].get("Calibration Date"), "result": r["fields"].get("Result"),
             "due": r["fields"].get("duedate")} for r in rows]


# --- actions ---------------------------------------------------------------------------------
def make_label(jira, key, f, printer=None, show=True):
    cls = f.get("Equipment Class") or ""
    lines = [f"{f.get('Manufacturer') or ''} {f.get('Model') or ''}".strip() or cls,
             f"S/N {f['Serial Number']}" if f.get("Serial Number") else cls]
    status = f.get("Calibration Status")
    if cls != "Test Rack":
        lines.append("CAL NOT REQUIRED" if status == cal.NOT_REQUIRED else f"Cal due {f.get('duedate') or '-'}")
    png, preview = labels.make_label(jira.browse_url(key), f["Equipment ID"], lines, HERE / "labels")
    try:
        jira.attach(key, preview, "image/png")
    except Exception as e:  # a missing attachment shouldn't fail the registration
        print(f"  (label not attached to Jira: {e})")
    print(f"  label: {png}")
    if printer:
        try:
            labels.print_label(png, printer)
            print(f"  printed on {printer}")
        except Exception as e:
            print(f"  print failed ({e}); label saved at {png}")
    elif show:
        labels.open_file(preview)


def record_calibration(jira, eq, date, result="Pass", interval=12, due=None, provider=None, cert=None):
    """Creates a Calibration Event under the equipment and refreshes its summary fields."""
    f = eq["fields"]
    due = due or cal.add_months(dt.date.fromisoformat(date), int(interval)).isoformat()
    ev = jira.create(EVENT, f"Calibration {date} · {f['Equipment ID']} · {result}",
                     {"Calibration Date": date, "Result": result, "Provider": provider},
                     ["calibration-event", tag("eqid", f["Equipment ID"])], due, eq["key"])
    if cert:
        jira.attach(ev, cert, "application/pdf" if str(cert).lower().endswith(".pdf") else "application/octet-stream")
    events = events_of(jira, eq["key"]) or [{"key": ev, "date": date, "result": result, "due": due}]
    latest = cal.latest_event(events)
    if latest and latest["date"] == date:  # an older event entered late never overwrites newer info
        status = cal.status_at(events, today(), True, conditional_policy())
        jira.update(eq["key"], {"Last Calibrated": date, "Calibration Status": status}, due)
        print(f"  {ev}: {result} on {date}, due {due} -> {f['Equipment ID']} is {status}")
    else:
        print(f"  {ev}: recorded; {f['Equipment ID']} already has a newer calibration ({latest['date']}), summary unchanged")
    return ev


def register(jira, a, printer=None, show=True):
    """a: class, mfr, model, sn, rack, slot, last_cal, interval, result, cnr, notes. Returns Jira key."""
    if a.get("sn"):
        dup = find_by_serial(jira, a["sn"])
        if dup:
            d = dup[0]
            print(f"Already registered: {d['fields'].get('Equipment ID')} ({d['key']})  {d['fields']['summary']}")
            return d["key"]
    rack = must_equipment(jira, a["rack"]) if a.get("rack") else None
    eqid = next_equipment_id(jira)
    is_rack = a["class"] == "Test Rack"
    location = ""
    if rack:
        location = f"{rack['fields']['Equipment ID']} {rack['fields']['summary']}" + (f", slot {a['slot']}" if a.get("slot") else "")
    elif a.get("location"):
        location = a["location"]
    status = cal.NOT_REQUIRED if (a.get("cnr") or is_rack) else cal.UNKNOWN
    summary = a.get("name") or f"{a['class']} · {a.get('mfr', '')} {a.get('model', '')}".strip() + (f" · S/N {a['sn']}" if a.get("sn") else "")
    lbls = ["equipment", tag("eqid", eqid), tag("class", a["class"])]
    if a.get("sn"):
        lbls.append(tag("sn", a["sn"]))
    if rack:
        lbls.append(tag("in", rack["fields"]["Equipment ID"]))
    fields = {"Equipment ID": eqid, "Equipment Class": a["class"], "Manufacturer": a.get("mfr"),
              "Model": a.get("model"), "Serial Number": a.get("sn"), "Location": location,
              "Calibration Status": status}
    key = jira.create(EQUIPMENT, summary, fields, lbls, description=a.get("notes") or None)
    print(f"Created {eqid} ({key}): {summary}\n  {jira.browse_url(key)}")
    if rack:
        jira.link(rack["key"], key)
    eq = {"key": key, "fields": dict(fields, summary=summary, duedate=None)}
    if a.get("last_cal") and status != cal.NOT_REQUIRED:
        record_calibration(jira, eq, a["last_cal"], a.get("result") or "Pass", a.get("interval") or 12,
                           provider=a.get("provider") or "Initial registration")
        eq = must_equipment(jira, key)
    make_label(jira, key, eq["fields"], printer, show)
    return key


def prompt_asset(args):
    quick = bool(args.sn)  # serial given on the command line: take defaults instead of prompting
    a = {"class": args.cls or ask("Equipment class", choices=[c for c in CLASSES if c != "Test Rack"])}
    a["mfr"] = args.mfr or ask("Manufacturer")
    a["model"] = args.model or ask("Model")
    a["sn"] = args.sn or ask("Serial number")
    a["rack"] = args.rack or (os.environ.get("DEFAULT_RACK") if quick else
                              ask("Installed in rack (Equipment ID, blank = none)", os.environ.get("DEFAULT_RACK") or "-"))
    a["rack"] = None if a["rack"] in ("-", "") else a["rack"]
    a["slot"] = args.slot or ("" if quick or not a["rack"] else input("Slot / channel (optional): ").strip())
    a["cnr"] = args.cnr or (not quick and not args.last_cal and ask("Needs calibration? (y/n)", "y").lower().startswith("n"))
    if not a["cnr"]:
        lc = args.last_cal or ("-" if quick else ask("Last calibration date YYYY-MM-DD (blank if unknown)", "-"))
        a["last_cal"] = None if lc == "-" else lc
        a["interval"] = args.interval or ("12" if quick or not a["last_cal"] else ask("Calibration interval, months", "12"))
    a["notes"] = args.notes or ""
    return a


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["setup", "add-rack", "add", "import", "cal", "check", "list", "label"])
    p.add_argument("target", nargs="?", help="rack name, CSV file, or Equipment ID / Jira key")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--printer", default=os.environ.get("LABEL_PRINTER"))
    p.add_argument("--class", dest="cls", choices=CLASSES)
    for f in ("mfr", "model", "sn", "rack", "slot", "last-cal", "interval", "notes", "location",
              "date", "result", "provider", "due", "cert", "at"):
        p.add_argument(f"--{f}")
    p.add_argument("--cnr", action="store_true", help="calibration not required")
    args = p.parse_args(argv)
    jira = connect(args.dry_run, HERE / "dryrun_jira.json")

    if args.cmd == "setup":
        print(f"Signed in as {jira.whoami()}")
        name, style = jira.check_project()
        print(f"Space {jira.project}: {name} ({style})")
        types = jira.issue_types()
        if type(jira).__name__ == "SimpleJira":
            print("Mode: simple (Task = equipment, Subtask = calibration event, details in the description)")
            miss = [t for t in ("Task", "Subtask") if t.lower() not in {n.lower() for n in types}]
            if miss:
                raise SystemExit(f"Space is missing work type(s) {miss}; enable them in Space settings > Work types.")
            print("OK")
            return
        miss_t = [t for t in (EQUIPMENT, EVENT) if t.lower() not in {n.lower() for n in types}]
        miss_f = jira.missing_fields(EQ_FIELDS + EV_FIELDS)
        print("Work types: " + ("OK" if not miss_t else "MISSING " + ", ".join(miss_t)))
        print("Fields:     " + ("OK" if not miss_f else "MISSING " + ", ".join(miss_f)))
        if miss_t or miss_f:
            raise SystemExit("Add the missing items in Space settings > Work types (names must match), then rerun.")
    elif args.cmd == "add-rack":
        name = args.target or ask("Rack name", "GT Test Rack")
        register(jira, {"class": "Test Rack", "name": name, "mfr": args.mfr, "model": args.model,
                        "sn": args.sn, "location": args.location, "notes": args.notes}, args.printer)
    elif args.cmd == "add":
        while True:
            register(jira, prompt_asset(args), args.printer)
            if args.sn or not ask("Add another? (y/n)", "n").lower().startswith("y"):
                break
    elif args.cmd == "import":
        with open(args.target, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                a = {k.strip().lower().replace("-", "_").replace(" ", "_"): (v or "").strip() for k, v in row.items()}
                a["cnr"] = a.get("cnr", "").lower() in ("y", "yes", "true", "1")
                a["rack"] = a.get("rack") or os.environ.get("DEFAULT_RACK") or None
                a["last_cal"] = a.get("last_cal") or None
                register(jira, a, args.printer, show=False)
    elif args.cmd == "cal":
        eq = must_equipment(jira, args.target)
        record_calibration(jira, eq, args.date or today(), args.result or "Pass", args.interval or 12,
                           args.due, args.provider, args.cert)
    elif args.cmd == "check":
        eq = must_equipment(jira, args.target)
        at = args.at or today()
        required = eq["fields"].get("Calibration Status") != cal.NOT_REQUIRED
        print(f"{eq['fields']['Equipment ID']} on {at}: "
              f"{cal.status_at(events_of(jira, eq['key']), at, required, conditional_policy())}")
    elif args.cmd == "list":
        jql = f'project = "{jira.project}" AND labels = "equipment"'
        if args.rack:
            jql += f' AND labels = "{tag("in", args.rack)}"'
        rows = jira.search(jql, ["summary", "duedate"] + EQ_FIELDS)
        for i in sorted(rows, key=lambda r: r["fields"].get("Equipment ID") or ""):
            f = i["fields"]
            print(f"{f.get('Equipment ID') or '-':13} {i['key']:8} {f.get('Calibration Status') or '-':12} "
                  f"due {f.get('duedate') or '-':10}  {f['summary']}")
    elif args.cmd == "label":
        eq = must_equipment(jira, args.target)
        make_label(jira, eq["key"], eq["fields"], args.printer)


if __name__ == "__main__":
    sys.exit(main())
