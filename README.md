# GT equipment registry (Jira + QR labels)

Type an instrument's identification once. The tool gives it a permanent Equipment ID, creates its
Jira record, records its calibration, and prints a QR label. Base Jira only (no Assets add-on).

## Jira model (Lab Equipment space, key LAB)

**No Jira setup needed.** By default the tool uses the space's standard **Task** (one per piece of
equipment) and **Subtask** (one per calibration event) and keeps the details in each description: a
readable table plus a small JSON block the tool reads back. If you later add the custom work types and
fields below, the tool switches to them automatically (`JIRA_MODE=auto|simple|fields` overrides).

Concepts (either mode):

| Item | What it is |
|---|---|
| **Equipment** work type | one per physical item, racks included. Fields: Equipment ID, Equipment Class, Manufacturer, Model, Serial Number, Location, Calibration Status, Last Calibrated; built-in **Due date** = next cal due |
| **Calibration Event** (sub-item of an Equipment issue) | one per calibration. Fields: Calibration Date, Result (Pass/Fail/Conditional), Provider; Due date = next due; certificate attached |
| Rack ↔ instrument | "Relates" link + `in-<rack id>` label + the Location field. Instruments keep their identity when moved |
| Epics | project work only (e.g. "Commission ET stand"), not equipment |

- **Equipment ID** (`GG-EQ-000012`) is permanent and goes on the label and into Sift. The Jira key is for navigation.
- **Serial number** is an attribute; it's also used for the duplicate check (`sn-…` label).
- **Calibration status is time-aware** (`calibration.py`): for a given date, only calibrations on or
  before that date count, so a later calibration never makes an earlier run look calibrated.
  Conditional results count as Unknown unless `CONDITIONAL_COUNTS_AS=valid`.
- A calibration entered late (older than the latest one) is kept as history but doesn't overwrite the summary.

### Optional: real fields (Space settings > Work types; names must match)

1. Add work type **Equipment** with fields: Short text *Equipment ID*, *Manufacturer*, *Model*, *Serial Number*,
   *Location*; Dropdown *Equipment Class* (Test Rack, Pressure Transducer, Load Cell, Load Cell Amplifier,
   Flow Controller, DAQ Chassis, DAQ Module, Thermocouple, Power Supply, Other); Dropdown *Calibration Status*
   (Valid, Expired, Failed, Not Required, Unknown); Date *Last Calibrated*.
2. Rename **Subtask** to **Calibration Event** and add: Date *Calibration Date*; Dropdown *Result* (Pass, Fail,
   Conditional); Short text *Provider*.

## Install (PC with the label printer)

```powershell
py -m pip install -r requirements.txt
copy .env.example .env        # fill in site, email, API token
py asset_tool.py setup        # checks access (and fields, if you added them)
py asset_tool.py add-rack "GT Test Rack"   # then set DEFAULT_RACK in .env to the ID it prints
```

## Use

```powershell
py asset_tool.py add                       # prompts for each field
py asset_tool.py add --class "Pressure Transducer" --mfr Omega --model PX309 --sn 48213 --slot PT1 --last-cal 2025-09-15
py asset_tool.py import assets.csv         # columns: class,mfr,model,sn,rack,slot,last_cal,interval,cnr,notes
py asset_tool.py cal GG-EQ-000002 --date 2026-10-09 --result Pass --provider "Omega" --cert cert.pdf
py asset_tool.py check GG-EQ-000002 --at 2026-10-01
py asset_tool.py list --rack GG-EQ-000001
py asset_tool.py label GG-EQ-000002        # reprint (e.g. after a recalibration changes the due date)
```

`--dry-run` on any command uses a local fake Jira. Tests: `py -m pytest tests`.

## Printer

Set `LABEL_PRINTER` to the Windows name of the Brother P-touch. Printing goes through the Windows driver
(pywin32) and is untested until the printer arrives; without it the label PNG opens and is saved in `labels/`.

## Not built yet

- Automatic discovery (waiting on what Revel can expose); it would call the same `register()`.
- Writing equipment IDs and cal status into Sift runs from the run bridge, using `calibration.status_at`.
