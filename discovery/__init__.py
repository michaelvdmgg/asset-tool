"""Read-only equipment discovery: adapters -> observations -> reconciliation -> report.

Nothing in this package writes to Jira or sends commands to hardware (the optional VISA *IDN?
identification query is off unless asked for).
"""
