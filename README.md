# GT asset registration tool

Type an instrument's identification once. The tool:

1. checks Jira for that serial number, so nothing is registered twice
2. creates a Jira task under the stand's "Test Rack" or "Sensors" epic (picked from the instrument type) (the task key, e.g. LAB-12, is the asset ID)
3. makes a QR label (12 mm tape, links to the Jira task), attaches it to the task, and prints it
   or opens it if no printer is set

Base Jira only, no Assets add-on. Layout in the **LAB** space:

| Jira item | Holds |
|---|---|
| Epic "GT Test Rack" | equipment mounted in the rack: cDAQ + modules, amps, supplies, SSR |
| Epic "GT Sensors" | sensors out on the stand: PTs, load cell, TCs, Alicats |
| Task under an epic | one instrument: type, manufacturer, model, S/N, subsystem, cal info in the description |
| Due date | calibration due date (blank = cal not required) |
| Labels | `asset`, `sn-…`, `type-…`, `subsys-…`, `stand-…`, `bucket-rack` / `bucket-sensors`, `cal-required` / `cal-not-required` |

## Setup (once, on the PC that has the label printer)

```powershell
cd gt-asset-tool
py -m pip install -r requirements.txt
copy .env.example .env      # then edit .env: site, email, API token
py asset_tool.py setup      # checks access, creates the "GT Test Rack" and "GT Sensors" epics
```

API token: id.atlassian.com > Security > Create and manage API tokens. It goes only in `.env`.

## Use

```powershell
py asset_tool.py add                      # prompts for each field
py asset_tool.py add --type "Pressure transducer" --mfr Omega --model PX309-1KGI --sn 48213 --last-cal 2025-09-30
py asset_tool.py import assets.csv        # columns: type,mfr,model,sn,subsystem,last_cal,interval,cnr,notes
py asset_tool.py list                     # assets on the stand, soonest cal due first
py asset_tool.py label LAB-12             # remake / reprint a label
```

Add `--dry-run` to try anything without touching Jira (uses a local `dryrun_jira.json`).

## Printer

Set `LABEL_PRINTER` in `.env` to the Windows printer name of the Brother P-touch. Printing goes
through the Windows driver (pywin32) and has not been tested on the real printer yet. Without a
printer the label PNG opens on screen and is saved in `labels/`.

## Later

If Revel exposes device identity (model/serial) through its API, a discovery step can call the same
`register()` function instead of the prompts.
