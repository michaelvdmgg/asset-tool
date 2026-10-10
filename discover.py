"""Read-only equipment discovery proof of concept (Phase 1). Writes a report; never writes to Jira.

  py discover.py                                   Windows devices on this PC + expected list
  py discover.py --revel https://galactic-gt-stand Revel Channels API too (or REVEL_URL in .env)
  py discover.py --revel-file latest_values.json   Revel channel list saved earlier
  py discover.py --visa-idn                        also send *IDN? to VISA instruments (USB/LAN/GPIB)
  py discover.py --jira                            look up existing Jira assets by serial (read-only)

Report: reports/discovery_<stand>_<time>.md and .json
"""
import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from asset_tool import load_env
from discovery import reconcile, report, revel, visa, windows

HERE = Path(__file__).resolve().parent


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stand", default=os.environ.get("DEFAULT_STAND", "gt"))
    p.add_argument("--inventory", help="expected-equipment CSV (default inventory/<stand>_expected.csv)")
    p.add_argument("--revel", default=os.environ.get("REVEL_URL"), help="Revel base URL")
    p.add_argument("--revel-file", help="saved latest_values JSON instead of a live Revel")
    p.add_argument("--no-windows", action="store_true", help="skip Windows device enumeration")
    p.add_argument("--visa-idn", action="store_true", help="send *IDN? to VISA (non-serial) instruments")
    p.add_argument("--jira", action="store_true", help="read-only lookup of existing assets by serial")
    p.add_argument("--out", default=str(HERE / "reports"))
    args = p.parse_args(argv)

    sources, observations, revel_notes = [], [], None

    def run(name, fn):
        try:
            obs, note = fn()
            observations.extend(obs)
            sources.append({"name": name, "result": f"{len(obs)} observations" + (f"; {note}" if note else "")})
            return obs
        except Exception as e:
            sources.append({"name": name, "result": f"FAILED: {type(e).__name__}: {e}"})

    if not args.no_windows:
        run("Windows PnP", lambda: (windows.discover(), None))
    run("VISA", lambda: visa.discover(idn=args.visa_idn))
    if args.revel_file or args.revel:
        def rv():
            nonlocal revel_notes
            obs, revel_notes = revel.discover(url=args.revel, path=args.revel_file)
            return obs, f"{revel_notes['channels_total']} channels read"
        run(f"Revel Channels API ({args.revel_file or args.revel})", rv)
    else:
        sources.append({"name": "Revel", "result": "skipped (no --revel / REVEL_URL)"})

    entries, conflicts = reconcile.merge(observations)
    if args.jira:
        from jira_client import from_env
        try:
            reconcile.jira_lookup(from_env(), entries)
            sources.append({"name": "Jira (read-only)", "result": "looked up serials"})
        except Exception as e:
            sources.append({"name": "Jira (read-only)", "result": f"FAILED: {e}"})

    inv_path = Path(args.inventory or HERE / "inventory" / f"{args.stand}_expected.csv")
    inventory = reconcile.load_inventory(inv_path) if inv_path.exists() else []
    sources.append({"name": "Expected list", "result": f"{inv_path.name}: {len(inventory)} items" if inventory
                    else f"none ({inv_path} not found)"})
    rows, extra = reconcile.match_inventory(entries, inventory)

    rep = report.build(rows, extra, conflicts, sources, revel_notes)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    md = report.markdown(rep, f"Equipment discovery: {args.stand.upper()} stand, {dt.datetime.now():%Y-%m-%d %H:%M}")
    md_path, json_path = report.write(rep, md, Path(args.out) / f"discovery_{args.stand}_{stamp}")
    cov = rep["coverage"]
    for s in sources:
        print(f"{s['name']}: {s['result']}")
    print(f"\nIdentified automatically: {cov['identified']} of {cov['expected']} expected items")
    for k, v in sorted(cov["by_result"].items()):
        print(f"  {k}: {v}")
    print(f"Unexpected devices: {len(rep['unexpected'])}   Conflicts: {len(conflicts)}")
    print(f"\nReport: {md_path}\n        {json_path}")


if __name__ == "__main__":
    sys.exit(main())
