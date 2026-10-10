"""GT asset registration: type an instrument's ID once -> Jira task + QR label.

  python asset_tool.py setup              check Jira access, create the stand's two epics
  python asset_tool.py add                register one instrument (prompts for anything missing)
  python asset_tool.py import assets.csv  register many from a CSV (same columns as the flags)
  python asset_tool.py list               show registered instruments and cal due dates
  python asset_tool.py label LAB-12       re-make / re-print the label for an existing asset

Add --dry-run to any command to use a local fake Jira (dryrun_jira.json) instead of the real one.
"""
import argparse
import calendar
import csv
import datetime as dt
import os
import re
import sys
from pathlib import Path

import labels
from jira_client import adf_doc, from_env

HERE = Path(__file__).resolve().parent

# Each stand gets two epics: equipment mounted in its test rack, and sensors out on the stand.
BUCKETS = {"rack": "Test Rack", "sensors": "Sensors"}
SENSOR_TYPES = {"Pressure transducer", "Load cell", "Thermocouple", "Mass flow controller", "Mass flow meter"}

TYPES = {  # instrument type -> default subsystem
    "Pressure transducer": "Pressure", "Load cell": "Force", "Load cell amplifier": "Force",
    "Mass flow controller": "Flow", "Mass flow meter": "Flow", "DAQ chassis": "DAQ",
    "DAQ module": "DAQ", "Thermocouple": "Temperature", "Power supply": "Power",
    "Electronic load": "Power", "Other": "Other",
}


def load_env(path=HERE / ".env"):
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def stands():
    spec = os.environ.get("STANDS", "gt:GT")
    return dict(p.split(":", 1) for p in spec.split(";") if ":" in p)


def bucket_for(itype):
    return "sensors" if itype in SENSOR_TYPES else "rack"


def epic_name(stand, bucket):
    return f"{stands().get(stand, stand.upper())} {BUCKETS[bucket]}"


def tag(prefix, value):
    return f"{prefix}-" + re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").lower()


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def ask(prompt, default=None, choices=None):
    if choices:
        for i, c in enumerate(choices, 1):
            print(f"  {i:2d}. {c}")
    while True:
        hint = f" [{default}]" if default else ""
        v = input(f"{prompt}{hint}: ").strip() or (default or "")
        if choices and v.isdigit() and 1 <= int(v) <= len(choices):
            return choices[int(v) - 1]
        if choices:
            hit = [c for c in choices if c.lower().startswith(v.lower())] if v else []
            if len(hit) == 1:
                return hit[0]
            print("  pick a number from the list")
            continue
        if v:
            return v


def stand_epic(jira, stand, bucket, create=False):
    found = jira.search(f'project = "{jira.project}" AND labels = "{tag("stand", stand)}" '
                        f'AND labels = "{tag("bucket", bucket)}" AND issuetype = Epic')
    if found:
        return found[0]["key"]
    if not create:
        raise SystemExit(f"No '{epic_name(stand, bucket)}' epic yet. Run: python asset_tool.py setup --stand {stand}")
    name = epic_name(stand, bucket)
    return jira.create("Epic", name, adf_doc([("Stand", stands().get(stand, stand)), ("Group", BUCKETS[bucket])],
                       f"Each {BUCKETS[bucket].lower()} item on this stand is a child task of this epic. "
                       "Moving an item to another stand = change its parent epic."),
                       [tag("stand", stand), tag("bucket", bucket)])


def find_by_serial(jira, sn):
    return jira.search(f'project = "{jira.project}" AND labels = "{tag("sn", sn)}"')


def label_lines(a):
    lines = [f"{a['model']}", f"S/N {a['sn']}"]
    lines.append("CAL NOT REQUIRED" if a.get("cnr") else f"Cal due {a['due'] or '-'}")
    return lines


def register(jira, a, rack, printer=None, show=True):
    """a: dict with type, mfr, model, sn, subsystem, last_cal, interval, cnr, notes. Returns key."""
    dup = find_by_serial(jira, a["sn"])
    if dup:
        k = dup[0]["key"]
        print(f"Already registered: {k}  {dup[0]['fields']['summary']}\n  {jira.browse_url(k)}")
        return k
    a["due"] = None
    if not a.get("cnr") and a.get("last_cal"):
        a["due"] = add_months(dt.date.fromisoformat(a["last_cal"]), int(a.get("interval") or 12)).isoformat()
    bucket = bucket_for(a["type"])
    epic = stand_epic(jira, rack, bucket)
    rows = [("Type", a["type"]), ("Manufacturer", a["mfr"]), ("Model", a["model"]),
            ("Serial number", a["sn"]), ("Subsystem", a["subsystem"]), ("Location", epic_name(rack, bucket)),
            ("Calibration", "Not required (CNR)" if a.get("cnr") else
             f"Last {a.get('last_cal') or 'unknown'}, every {a.get('interval') or 12} months, due {a['due'] or 'unknown'}")]
    if a.get("notes"):
        rows.append(("Notes", a["notes"]))
    lbls = ["asset", tag("sn", a["sn"]), tag("type", a["type"]), tag("subsys", a["subsystem"]),
            tag("stand", rack), tag("bucket", bucket), "cal-not-required" if a.get("cnr") else "cal-required"]
    summary = f"{a['type']} · {a['mfr']} {a['model']} · S/N {a['sn']}"
    key = jira.create("Task", summary, adf_doc(rows, "Registered with asset_tool.py"), lbls, a["due"], epic)
    url = jira.browse_url(key)
    png, preview = labels.make_label(url, key, label_lines(a), HERE / "labels")
    try:
        jira.attach(key, preview)
    except Exception as e:  # attachment is a nice-to-have
        print(f"  (label not attached to Jira: {e})")
    print(f"Created {key}: {summary}\n  {url}\n  label: {png}")
    if printer:
        try:
            labels.print_label(png, printer)
            print(f"  printed on {printer}")
        except Exception as e:
            print(f"  print failed ({e}); label saved at {png}")
    elif show:
        labels.open_file(preview)
    return key


def prompt_asset(args):
    a = {}
    quick = bool(args.sn)  # serial given on the command line: use defaults instead of prompting
    a["type"] = args.type or ask("Instrument type", choices=list(TYPES))
    a["mfr"] = args.mfr or ask("Manufacturer")
    a["model"] = args.model or ask("Model")
    a["sn"] = args.sn or ask("Serial number")
    a["subsystem"] = args.subsystem or (TYPES.get(a["type"], "Other") if quick
                                        else ask("Subsystem", TYPES.get(a["type"], "Other")))
    a["cnr"] = args.cnr or (not quick and not args.last_cal
                            and ask("Needs calibration? (y/n)", "y").lower().startswith("n"))
    if not a["cnr"]:
        a["last_cal"] = args.last_cal or ("-" if quick else ask("Last cal date YYYY-MM-DD (blank if unknown)", "-"))
        a["last_cal"] = None if a["last_cal"] == "-" else a["last_cal"]
        a["interval"] = args.interval or ("12" if quick else ask("Cal interval, months", "12"))
    a["notes"] = args.notes or ""
    return a


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["setup", "add", "import", "list", "label"])
    p.add_argument("target", nargs="?", help="CSV file for import, issue key for label")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stand", dest="rack", default=os.environ.get("DEFAULT_STAND", "gt"))
    p.add_argument("--printer", default=os.environ.get("LABEL_PRINTER"))
    for f in ("type", "mfr", "model", "sn", "subsystem", "last-cal", "interval", "notes"):
        p.add_argument(f"--{f}")
    p.add_argument("--cnr", action="store_true", help="calibration not required")
    args = p.parse_args(argv)
    jira = from_env(args.dry_run, HERE / "dryrun_jira.json")

    if args.cmd == "setup":
        print(f"Signed in as {jira.whoami()}")
        name, style = jira.check_project()
        print(f"Project {jira.project}: {name} ({style})")
        for b in BUCKETS:
            print(f"Epic '{epic_name(args.rack, b)}': {stand_epic(jira, args.rack, b, create=True)}")
    elif args.cmd == "add":
        while True:
            register(jira, prompt_asset(args), args.rack, args.printer)
            if any([args.sn, args.model]) or not ask("Add another? (y/n)", "n").lower().startswith("y"):
                break
    elif args.cmd == "import":
        with open(args.target, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {k.strip().lower().replace("-", "_").replace(" ", "_"): (v or "").strip() for k, v in row.items()}
                row["cnr"] = row.get("cnr", "").lower() in ("y", "yes", "true", "1")
                row["subsystem"] = row.get("subsystem") or TYPES.get(row.get("type"), "Other")
                row["last_cal"] = row.get("last_cal") or None
                register(jira, row, args.rack, args.printer, show=False)
    elif args.cmd == "list":
        for i in jira.search(f'project = "{jira.project}" AND labels = "asset" AND labels = "{tag("stand", args.rack)}" ORDER BY duedate ASC'):
            f = i["fields"]
            print(f"{i['key']:8} due {f.get('duedate') or '-':10}  {f['summary']}")
    elif args.cmd == "label":
        found = jira.search(f'key = "{args.target}"', fields=("summary", "duedate", "labels"))
        if not found:
            raise SystemExit(f"{args.target} not found")
        f = found[0]["fields"]
        m = re.match(r".*· (.*) · S/N (.*)$", f["summary"])
        a = {"model": m.group(1) if m else "", "sn": m.group(2) if m else "", "due": f.get("duedate"),
             "cnr": "cal-not-required" in f.get("labels", [])}
        png, preview = labels.make_label(jira.browse_url(args.target), args.target, label_lines(a), HERE / "labels")
        print(f"label: {png}")
        if args.printer:
            labels.print_label(png, args.printer)
        else:
            labels.open_file(preview)


if __name__ == "__main__":
    sys.exit(main())
