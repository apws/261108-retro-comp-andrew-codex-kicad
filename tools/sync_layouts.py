#!/usr/bin/env python3
"""Synchronize the current single-file authoritative schematic into layout projects.

Default: read-only status check. --sync initializes missing project resources and
copies the schematic, preserving its bytes/UUIDs and each layout's existing PCB
and project settings. Existing schematic snapshots must match their manifests.
"""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "Z80_Simple_Computer"
SOURCE = ROOT / "design" / f"{BASE}.kicad_sch"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    temporary = path.with_name(path.name + ".sync-tmp")
    if temporary.exists():
        raise ValueError(f"review leftover temporary file: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="read-only status (default)")
    actions.add_argument("--sync", action="store_true", help="update unfrozen snapshots")
    parser.add_argument("--layout", action="append", help="layout folder name; repeat to select several")
    args = parser.parse_args()

    source_data = SOURCE.read_bytes()
    source_hash = sha(source_data)
    # A hierarchy needs all child sheets snapshotted; reject one until supported.
    import re
    if re.search(rb'\(sheet\s+\(', source_data):
        raise ValueError("child sheets detected; extend synchronization before proceeding")
    source_project = SOURCE.with_suffix(".kicad_pro").read_bytes()
    tables = {name: (ROOT / name).read_bytes() for name in ("fp-lib-table", "sym-lib-table")}
    manifests = sorted((ROOT / "layouts").glob("*/experiment.json"))
    available = {p.parent.name for p in manifests}
    selected = set(args.layout or available)
    unknown = selected - available
    if unknown:
        raise ValueError(f"unknown layouts: {', '.join(sorted(unknown))}")
    if not selected:
        raise ValueError("no layout manifests found")
    if args.sync and list(SOURCE.parent.glob("*.lck")):
        raise ValueError("authoritative project has a KiCad lock; close it before synchronization")

    jobs = []
    failed = False
    for manifest_path in manifests:
        folder = manifest_path.parent
        if folder.name not in selected:
            continue
        info = json.loads(manifest_path.read_text())
        destination = folder / SOURCE.name
        recorded = (info.get("schematic_snapshot") or {}).get("sha256")
        current = sha(destination.read_bytes()) if destination.exists() else None
        frozen = info.get("frozen", False)
        problems = []
        if current is not None and current != recorded:
            problems.append("snapshot locally edited or missing provenance")
        if current is None and recorded is not None:
            problems.append("recorded snapshot is missing")
        if args.sync and not frozen and list(folder.glob("*.lck")):
            problems.append("KiCad project is open (lock file present)")
        link = folder / "libs"
        if link.is_symlink() or link.exists():
            if not link.is_symlink() or link.resolve() != (ROOT / "libs").resolve():
                problems.append("libs must link to the shared root libraries")
        for name, data in tables.items():
            path = folder / name
            if path.exists() and path.read_bytes() != data:
                problems.append(f"{name} differs from the shared configuration")
        project = folder / f"{BASE}.kicad_pro"
        resources_missing = not project.exists() or not link.exists() or any(
            not (folder / name).exists() for name in tables
        )
        state = "CURRENT" if current == source_hash else "NEEDS SYNC"
        if frozen:
            state = "FROZEN" if current else "FROZEN WITHOUT SNAPSHOT"
        if resources_missing:
            state += "; RESOURCES MISSING"
        if problems:
            state = "ERROR: " + "; ".join(problems)
        print(f"{folder.name}: {state}")
        failed |= bool(problems) or (not args.sync and (resources_missing or (not frozen and current != source_hash)))
        jobs.append((manifest_path, info, current, frozen, resources_missing, problems))

    # Validate every selected destination before any mutation.
    if failed:
        return 1
    if not args.sync:
        return 0
    for manifest_path, info, current, frozen, resources_missing, _ in jobs:
        if frozen:
            continue
        folder = manifest_path.parent
        project = folder / f"{BASE}.kicad_pro"
        if not project.exists():
            atomic_write(project, source_project)
        if not (folder / "libs").is_symlink():
            (folder / "libs").symlink_to("../../libs", target_is_directory=True)
        for name, data in tables.items():
            if not (folder / name).exists():
                atomic_write(folder / name, data)
        if current != source_hash:
            atomic_write(folder / SOURCE.name, source_data)
            info["schematic_snapshot"] = {
                "source": SOURCE.relative_to(ROOT).as_posix(),
                "sha256": source_hash,
                "synced_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            if (folder / f"{BASE}.kicad_pcb").exists():
                info["board_status"] = "needs-schematic-update-and-verification"
            atomic_write(manifest_path, (json.dumps(info, indent=2) + "\n").encode())
            print(f"  synchronized {folder.name}: {source_hash}")
        elif resources_missing:
            print(f"  initialized resources for {folder.name}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(f"sync failed: {error}")
