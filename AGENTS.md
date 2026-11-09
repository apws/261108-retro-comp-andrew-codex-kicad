# Andrew Z80 project reminders

Read `docs/261109-1549-REVIEW-human-schematic-corrections.adoc`, especially the compact reminders, before editing the wired schematic.

Also read `docs/261109-2306-REVIEW-Mark-schematic-readability.adoc` before a drawing cleanup. Keep our own arrangement; learn readability principles without copying Andrew's schematic. Reserve visible text, complete pin graphics and pin numbers as well as symbol bodies. Use one outward wire per terminal and put branch junctions in open wire space. Six inward wire continuations were identified in the original machine drawing; native ERC does not detect this presentation defect.

The cleanup is now implemented: read `docs/261110-0001-DONE-schematic-7805-and-readable-wiring.adoc`. Preserve `schematics/02-wired-a3/` (301 assignments) and its separate `schematics/03-schematic-7805/` extension (315 assignments). Use `tools/check_schematic_7805.py` for the PSU delta and annotation checks; `tools/check_schematic_pin_clearance.py` checks both drawings. In the PSU variant J5 is +5V OUTPUT and J6 is the nominal 9V input; do not confuse those functions.

Read `docs/261110-0004-DONE-FirstZ80-fixed-placement-routing.adoc` before changing the new `firstz80/` series. Its common checkpoint and three completed boards are frozen separately from the earlier layouts. The second result is an octilinear HYBRID (fresh orthogonal seed plus checked 45-degree refinements); ten direct eight-direction trials were incomplete. FreeRouting used local MCP with optimization disabled and one disclosed native hole-clearance repair. Do not describe either result more strongly. Use `tools/check_firstz80.py`; retain the source schematic's 47 warnings separately from the zero PCB findings.

- `design/` remains the authoritative label-oriented circuit. Keep the common checkpoint and completed `layouts/` boards/schematic snapshots intact.
- `schematics/02-wired-a3/` is now human-edited by Falken. Preserve its layout; do not regenerate it or change the generator's original hash to bypass its overwrite guard.
- Preserve the saved input before repairs. Compare semantic objects and freshly exported native netlists, not only raw S-expression diffs.
- After schematic cleanup, run native ERC with all severities, export the netlist and run `tools/check_wired_schematic.py`. Use `--accept-manual` only for an intentional review after fresh exports; its checks must all pass. Do not relax rules or exclusions for cosmetic edits.
- Verify label anchors and moved supply-wire joins. C22 is +5V/GND, C24 pin 1 is GND and pin 2 is VS_MINUS, and DTR_TTL connects U5.33 to U6.10 independently of +5V.
- Refresh and visually inspect the SVG/PNG/PDF after validated changes. Favor clear human communication and retain the user's refinements.
- Notes in `docs/` use `YYMMDD-HHMM-...adoc` and `:title: {docname}`; retain Falken / mixworx.net rendering conventions.
- Leave unrelated files and editor history untouched and unstaged.
