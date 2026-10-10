"""GT Equipment: a small window over asset_tool.py. Add equipment, log calibrations, see the list.

  pyw asset_gui.py        (or double-click "GT Equipment.bat")

Uses the same .env and the same functions as the command line, so both stay in step.
"""
import contextlib
import datetime as dt
import io
import os
import queue
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

import asset_tool as at
import calibration as cal
from jira_client import connect

STATUS_COLORS = {cal.VALID: "#2e9d55", cal.EXPIRED: "#d64545", cal.FAILED: "#d64545",
                 cal.UNKNOWN: "#d99a1e", cal.NOT_REQUIRED: "#8a8f98"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        at.load_env()
        self.title("GT Equipment")
        self.geometry("980x700")
        self.minsize(860, 600)
        try:
            import sv_ttk
            sv_ttk.set_theme("light")
        except ImportError:
            pass
        self.jobs = queue.Queue()
        self.busy = False
        self.jira = {}
        self.racks, self.equipment = {}, {}
        self.preview_img = None
        self.practice = tk.BooleanVar(value=False)
        self._style()
        self._build()
        self.after(100, self._poll)
        self.refresh()

    # --- look ----------------------------------------------------------------------------
    def _style(self):
        s = ttk.Style(self)
        base = ("Segoe UI", 10)
        s.configure(".", font=base)
        s.configure("Hint.TLabel", foreground="#6b7280", font=("Segoe UI", 9))
        s.configure("Result.TLabel", font=("Segoe UI Semibold", 11))
        s.configure("Treeview", rowheight=26)

    def _build(self):
        # plain tk widgets here: the ttk theme repaints label backgrounds white
        bg = "#1f2a44"
        head = tk.Frame(self, bg=bg, padx=18, pady=12)
        head.pack(fill="x")
        tk.Label(head, text="GT Equipment Registry", bg=bg, fg="white", font=("Segoe UI Semibold", 15)).pack(side="left")
        self.conn = tk.Label(head, text="Connecting…", bg=bg, fg="#b8c2d9", font=("Segoe UI", 9))
        self.conn.pack(side="left", padx=16)
        tk.Checkbutton(head, text="Practice mode (no Jira)", variable=self.practice, command=self.refresh,
                       bg=bg, fg="#b8c2d9", selectcolor=bg, activebackground=bg, activeforeground="white",
                       font=("Segoe UI", 9), bd=0, highlightthickness=0).pack(side="right")

        self.tabs = ttk.Notebook(self, padding=(14, 10))
        self.tabs.pack(fill="both", expand=True)
        self.tabs.add(self._add_tab(), text="  Add equipment  ")
        self.tabs.add(self._cal_tab(), text="  Log calibration  ")
        self.tabs.add(self._list_tab(), text="  Equipment list  ")

        foot = ttk.Frame(self, padding=(14, 0, 14, 10))
        foot.pack(fill="x")
        self.status = ttk.Label(foot, text="", style="Hint.TLabel")
        self.status.pack(side="left")

    def _row(self, parent, r, label, widget, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))
        widget.grid(row=r, column=1, sticky="ew", pady=5)
        if hint:
            ttk.Label(parent, text=hint, style="Hint.TLabel").grid(row=r, column=2, sticky="w", padx=8)
        return widget

    # --- add -------------------------------------------------------------------------------
    def _add_tab(self):
        f = ttk.Frame(self.tabs, padding=16)
        form = ttk.Frame(f)
        form.pack(side="left", fill="y", anchor="n")
        form.columnconfigure(1, minsize=260)
        self.v = {k: tk.StringVar() for k in ("class", "mfr", "model", "sn", "rack", "slot", "last_cal", "interval", "notes")}
        self.v["interval"].set("12")
        self.needs_cal = tk.BooleanVar(value=True)
        classes = [c for c in at.CLASSES if c != "Test Rack"]
        self._row(form, 0, "Type", ttk.Combobox(form, textvariable=self.v["class"], values=classes, state="readonly"))
        self._row(form, 1, "Manufacturer", ttk.Entry(form, textvariable=self.v["mfr"]))
        self._row(form, 2, "Model", ttk.Entry(form, textvariable=self.v["model"]))
        self._row(form, 3, "Serial number", ttk.Entry(form, textvariable=self.v["sn"]), "checked for duplicates")
        self.rack_box = self._row(form, 4, "Rack", ttk.Combobox(form, textvariable=self.v["rack"], state="readonly"))
        self._row(form, 5, "Slot / channel", ttk.Entry(form, textvariable=self.v["slot"]), "optional")
        ttk.Checkbutton(form, text="Needs calibration", variable=self.needs_cal,
                        command=self._toggle_cal).grid(row=6, column=1, sticky="w", pady=(10, 4))
        self.lc = self._row(form, 7, "Last calibrated", ttk.Entry(form, textvariable=self.v["last_cal"]), "YYYY-MM-DD, blank if unknown")
        self.iv = self._row(form, 8, "Interval (months)", ttk.Spinbox(form, from_=1, to=60, textvariable=self.v["interval"], width=6))
        self.iv.grid(sticky="w")
        self._row(form, 9, "Notes", ttk.Entry(form, textvariable=self.v["notes"]))
        btns = ttk.Frame(form)
        btns.grid(row=10, column=1, sticky="w", pady=(16, 0))
        self.add_btn = ttk.Button(btns, text="Add to Jira", style="Accent.TButton", command=self.add)
        self.add_btn.pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear).pack(side="left", padx=8)

        res = ttk.Frame(f, padding=(28, 0, 0, 0))
        res.pack(side="left", fill="both", expand=True, anchor="n")
        self.res_title = ttk.Label(res, text="", style="Result.TLabel", wraplength=420, justify="left")
        self.res_title.pack(anchor="w")
        self.res_link = ttk.Label(res, text="", foreground="#2563eb", cursor="hand2")
        self.res_link.pack(anchor="w", pady=(4, 0))
        self.res_link.bind("<Button-1>", lambda e: self.res_link.url and webbrowser.open(self.res_link.url))
        self.res_link.url = None
        self.res_detail = ttk.Label(res, text="Fill in the form and press Add to Jira.\nThe QR label appears here.",
                                    style="Hint.TLabel", wraplength=420, justify="left")
        self.res_detail.pack(anchor="w", pady=(6, 12))
        self.preview = ttk.Label(res)
        self.preview.pack(anchor="w")
        return f

    def _toggle_cal(self):
        state = "normal" if self.needs_cal.get() else "disabled"
        self.lc.configure(state=state)
        self.iv.configure(state=state)

    def clear(self):
        for k, var in self.v.items():
            if k not in ("rack", "interval", "class"):
                var.set("")

    def add(self):
        a = {k: var.get().strip() for k, var in self.v.items()}
        if not a["class"]:
            return self.say("Pick a type.", error=True)
        if not (a["sn"] or a["model"]):
            return self.say("Enter at least a serial number or a model.", error=True)
        if self.needs_cal.get() and a["last_cal"] and not DATE_RE.match(a["last_cal"]):
            return self.say("Last calibrated must look like 2026-09-15.", error=True)
        a["rack"] = self.racks.get(a["rack"])
        a["cnr"] = not self.needs_cal.get()
        a["last_cal"] = a["last_cal"] or None if self.needs_cal.get() else None
        a["interval"] = a["interval"] or "12"

        def job(jira):
            key = at.register(jira, a, os.environ.get("LABEL_PRINTER"), show=False)
            return key, at.must_equipment(jira, key)
        self.run(job, self._added, "Adding to Jira…")

    def _added(self, result, out):
        key, eq = result
        f = eq["fields"]
        dup = "Already registered" in out
        self.res_title.configure(text=("Already in Jira: " if dup else "Added: ") + f"{f.get('Equipment ID')}  ·  {f['summary']}")
        self.res_link.url = self.jira_now().browse_url(key)
        self.res_link.configure(text=f"Open {key} in Jira ↗")
        st = f.get("Calibration Status") or "-"
        self.res_detail.configure(text=f"Calibration: {st}" + (f", due {f['duedate']}" if f.get("duedate") else ""),
                                  foreground=STATUS_COLORS.get(st, "#6b7280"))
        self._show_label(f.get("Equipment ID"))
        if not dup:
            self.clear()
        self.say(out.strip().splitlines()[0] if out.strip() else "Done.")
        self.refresh(quiet=True)

    def _show_label(self, eqid):
        p = at.HERE / "labels" / f"{eqid}_preview.png"
        if not p.exists():
            self.preview.configure(image="")
            return
        img = Image.open(p)
        img.thumbnail((440, 200))
        self.preview_img = ImageTk.PhotoImage(img)
        self.preview.configure(image=self.preview_img)

    # --- calibration -----------------------------------------------------------------------
    def _cal_tab(self):
        f = ttk.Frame(self.tabs, padding=16)
        form = ttk.Frame(f)
        form.pack(anchor="nw")
        form.columnconfigure(1, minsize=380)
        self.c = {"eq": tk.StringVar(), "date": tk.StringVar(value=at.today()), "result": tk.StringVar(value="Pass"),
                  "provider": tk.StringVar(), "interval": tk.StringVar(value="12"), "cert": tk.StringVar()}
        self.eq_box = self._row(form, 0, "Equipment", ttk.Combobox(form, textvariable=self.c["eq"], state="readonly"))
        self._row(form, 1, "Calibration date", ttk.Entry(form, textvariable=self.c["date"]), "YYYY-MM-DD")
        self._row(form, 2, "Result", ttk.Combobox(form, textvariable=self.c["result"], values=at.RESULTS, state="readonly", width=14)).grid(sticky="w")
        self._row(form, 3, "Provider", ttk.Entry(form, textvariable=self.c["provider"]), "optional")
        self._row(form, 4, "Interval (months)", ttk.Spinbox(form, from_=1, to=60, textvariable=self.c["interval"], width=6)).grid(sticky="w")
        cert = ttk.Frame(form)
        ttk.Entry(cert, textvariable=self.c["cert"]).pack(side="left", fill="x", expand=True)
        ttk.Button(cert, text="Browse…", command=lambda: self.c["cert"].set(
            filedialog.askopenfilename(filetypes=[("Certificates", "*.pdf *.png *.jpg"), ("All files", "*.*")]) or self.c["cert"].get())
        ).pack(side="left", padx=(6, 0))
        self._row(form, 5, "Certificate", cert, "optional")
        self.cal_btn = ttk.Button(form, text="Log calibration", style="Accent.TButton", command=self.log_cal)
        self.cal_btn.grid(row=6, column=1, sticky="w", pady=(16, 0))
        self.cal_result = ttk.Label(form, text="", style="Result.TLabel", wraplength=560, justify="left")
        self.cal_result.grid(row=7, column=0, columnspan=3, sticky="w", pady=(18, 0))
        return f

    def log_cal(self):
        c = {k: v.get().strip() for k, v in self.c.items()}
        ref = self.equipment.get(c["eq"])
        if not ref:
            return self.say("Pick the equipment.", error=True)
        if not DATE_RE.match(c["date"]):
            return self.say("Calibration date must look like 2026-09-15.", error=True)

        def job(jira):
            eq = at.must_equipment(jira, ref)
            at.record_calibration(jira, eq, c["date"], c["result"], c["interval"] or 12, None,
                                  c["provider"] or None, c["cert"] or None)
            return at.must_equipment(jira, ref)
        self.run(job, self._cal_done, "Logging calibration…")

    def _cal_done(self, eq, out):
        f = eq["fields"]
        st = f.get("Calibration Status") or "-"
        self.cal_result.configure(text=f"{f.get('Equipment ID')} is now {st}" + (f", due {f['duedate']}" if f.get("duedate") else ""),
                                  foreground=STATUS_COLORS.get(st, "#111"))
        self.c["cert"].set("")
        self.say(out.strip().splitlines()[-1] if out.strip() else "Done.")
        self.refresh(quiet=True)

    # --- list ------------------------------------------------------------------------------
    def _list_tab(self):
        f = ttk.Frame(self.tabs, padding=16)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(bar, text="Open in Jira", command=self.open_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="Show / print label", command=self.label_selected).pack(side="left")
        ttk.Label(bar, text="Double-click a row to open it in Jira", style="Hint.TLabel").pack(side="right")
        cols = ("id", "key", "status", "due", "what")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, (title, w) in zip(cols, [("Equipment ID", 120), ("Jira", 70), ("Calibration", 110), ("Due", 100), ("Equipment", 480)]):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, stretch=(c == "what"))
        for st, color in STATUS_COLORS.items():
            self.tree.tag_configure(st, foreground=color)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
        return f

    def selected_key(self):
        sel = self.tree.selection()
        return self.tree.item(sel[0], "values")[1] if sel else None

    def open_selected(self):
        k = self.selected_key()
        if k:
            webbrowser.open(self.jira_now().browse_url(k))

    def label_selected(self):
        k = self.selected_key()
        if not k:
            return self.say("Select a row first.", error=True)

        def job(jira):
            eq = at.must_equipment(jira, k)
            at.make_label(jira, eq["key"], eq["fields"], os.environ.get("LABEL_PRINTER"), show=True)
        self.run(job, lambda r, out: self.say(out.strip().splitlines()[-1] if out.strip() else "Label made."), "Making label…")

    def refresh(self, quiet=False):
        def job(jira):
            who = jira.whoami()
            rows = jira.search(f'project = "{jira.project}" AND labels = "equipment"', ["summary", "duedate"] + at.EQ_FIELDS)
            return who, jira.project, sorted(rows, key=lambda r: r["fields"].get("Equipment ID") or "")
        self.run(job, self._refreshed, None if quiet else "Loading equipment…")

    def _refreshed(self, result, out):
        who, project, rows = result
        mode = "PRACTICE (local file, not Jira)" if self.practice.get() else f"Jira space {project}"
        self.conn.configure(text=f"{who}  ·  {mode}  ·  {len(rows)} items")
        self.tree.delete(*self.tree.get_children())
        self.racks, self.equipment = {}, {}
        for r in rows:
            f = r["fields"]
            st = f.get("Calibration Status") or "-"
            eqid = f.get("Equipment ID") or r["key"]
            self.tree.insert("", "end", values=(eqid, r["key"], st, f.get("duedate") or "-", f["summary"]), tags=(st,))
            label = f"{eqid}  ·  {f['summary']}"
            (self.racks if f.get("Equipment Class") == "Test Rack" else self.equipment)[label] = eqid
            self.equipment.setdefault(label, eqid)
        self.rack_box.configure(values=["(none)"] + list(self.racks))
        if not self.v["rack"].get() or self.v["rack"].get() not in self.racks:
            default = os.environ.get("DEFAULT_RACK")
            self.v["rack"].set(next((l for l, i in self.racks.items() if i == default), "(none)"))
        self.eq_box.configure(values=[l for l in self.equipment if l not in self.racks])
        if self.status.cget("text") == "Loading equipment…":
            self.say("Ready.")

    # --- plumbing --------------------------------------------------------------------------
    def jira_now(self):
        mode = self.practice.get()
        if mode not in self.jira:
            self.jira[mode] = connect(mode, at.HERE / "dryrun_jira.json")
        return self.jira[mode]

    def run(self, fn, done, msg):
        if self.busy:
            return self.say("Still working on the last request…")
        self.busy = True
        self.config(cursor="watch")
        for b in (self.add_btn, self.cal_btn):
            b.state(["disabled"])
        if msg:
            self.say(msg)

        def work():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    r = fn(self.jira_now())
                self.jobs.put((done, r, buf.getvalue(), None))
            except BaseException as e:  # SystemExit from the tool carries a readable message
                self.jobs.put((done, None, buf.getvalue(), str(e) or type(e).__name__))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, r, out, err = self.jobs.get_nowait()
                self.busy = False
                self.config(cursor="")
                for b in (self.add_btn, self.cal_btn):
                    b.state(["!disabled"])
                if err:
                    self.say(err, error=True)
                else:
                    done(r, out)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def say(self, text, error=False):
        self.status.configure(text=text, foreground="#d64545" if error else "#6b7280")


if __name__ == "__main__":
    App().mainloop()
