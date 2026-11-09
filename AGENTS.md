# Andrew Z80 project reminders

Read `docs/261109-1549-REVIEW-human-schematic-corrections.adoc`, especially the compact reminders, before editing the wired schematic.

- `design/` remains the authoritative label-oriented circuit. Keep the common checkpoint and completed `layouts/` boards/schematic snapshots intact.
- `schematics/02-wired-a3/` is now human-edited by Falken. Preserve its layout; do not regenerate it or change the generator's original hash to bypass its overwrite guard.
- Preserve the saved input before repairs. Compare semantic objects and freshly exported native netlists, not only raw S-expression diffs.
- After schematic cleanup, run native ERC with all severities, export the netlist and run `tools/check_wired_schematic.py`. Use `--accept-manual` only for an intentional review after fresh exports; its checks must all pass. Do not relax rules or exclusions for cosmetic edits.
- Verify label anchors and moved supply-wire joins. C22 is +5V/GND, C24 pin 1 is GND and pin 2 is VS_MINUS, and DTR_TTL connects U5.33 to U6.10 independently of +5V.
- Refresh and visually inspect the SVG/PNG/PDF after validated changes. Favor clear human communication and retain the user's refinements.
- Notes in `docs/` use `YYMMDD-HHMM-...adoc` and `:title: {docname}`; retain Falken / mixworx.net rendering conventions.
- Leave unrelated files and editor history untouched and unstaged.
