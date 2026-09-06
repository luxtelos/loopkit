# triage.md — the work queue

Items land here after discovery. The loop only implements unattended work when a
spec exists and the row is marked `spec-ready`. Status values:
`new, spec-draft, spec-ready, fixing, pr-open, blocked, inbox, done`.

`blocked` = waiting on a human ruling in `inbox/needs-human.md`; counted, never
polled, never served as a stage. Rows are written through `triage_state.py`,
never by hand — a finding containing `|` is escaped on write and unescaped on
read, and a hand edit breaks that.

| finding | source | priority | spec | status |
|---|---|---|---|---|
