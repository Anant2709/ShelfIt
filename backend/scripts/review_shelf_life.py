"""Review shelf lives the system worked out, and promote the good ones.

This is the human half of a loop the machine cannot close on its own, and the
reason the app never writes to `data/shelf_life.json` at runtime: the system
*proposes*, a person *commits*. The curated file therefore stays human-authored
and version-controlled, while still growing over time -- and because it is
committed, a fresh install inherits everything already curated.

It mirrors the image-labelling flow. In both cases the system does the work, flags
what it is unsure of, and a human's decision becomes permanent ground truth.

Usage, from the backend/ directory:

    python -m scripts.review_shelf_life                     # list what is pending
    python -m scripts.review_shelf_life --all               # include confirmed
    python -m scripts.review_shelf_life --stale             # anchors that have drifted
    python -m scripts.review_shelf_life --approve "baby spinach"
    python -m scripts.review_shelf_life --approve-anchored  # bulk-approve anchored entries
    python -m scripts.review_shelf_life --reject "coconut milk"
    python -m scripts.review_shelf_life --correct "coconut milk" 730
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import settings
from app.services.learned_store import LearnedEntry, LearnedShelfLifeStore
from app.services.shelf_life import reset_dataset_cache


def load_curated() -> dict[str, int]:
    path = Path(settings.shelf_life_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_curated(curated: dict[str, int]) -> None:
    """Write the curated file back, sorted and newline-terminated.

    Sorted so promoting an entry produces a small, readable git diff rather than
    reordering the whole file.
    """
    path = Path(settings.shelf_life_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {name: curated[name] for name in sorted(curated)}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(ordered, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    reset_dataset_cache()


def describe(entry: LearnedEntry) -> str:
    origin = (
        f"like {entry.anchor} ({entry.anchor_days}d)"
        if entry.is_anchored
        else "estimated, no reference"
    )
    mark = "confirmed" if entry.confirmed else "pending"
    return f"  {entry.name:<28} {entry.days:>5}d   {origin:<32} {mark}"


def promote(store: LearnedShelfLifeStore, name: str) -> bool:
    entry = store.get(name)
    if entry is None:
        print(f"No learned entry named {name!r}.")
        return False

    curated = load_curated()
    curated[entry.name] = entry.days
    save_curated(curated)
    store.confirm(entry.name)
    print(f"Promoted {entry.name!r} = {entry.days}d into the curated file.")
    return True


def command_list(store: LearnedShelfLifeStore, show_all: bool) -> None:
    entries = store.all() if show_all else store.pending()
    if not entries:
        print("Nothing to review." if not show_all else "No learned entries yet.")
        return

    anchored = sum(1 for entry in entries if entry.is_anchored)
    label = "learned" if show_all else "pending"
    print(f"{len(entries)} {label} entr{'y' if len(entries) == 1 else 'ies'}:\n")
    for entry in entries:
        print(describe(entry))
    print(
        f"\n{anchored} derived from a known item, "
        f"{len(entries) - anchored} estimated without a reference."
    )
    print("Anchored entries are usually safe to approve in bulk.")


def command_stale(store: LearnedShelfLifeStore) -> None:
    """Entries whose anchor no longer holds the value they were derived from."""
    stale = store.stale(load_curated())
    if not stale:
        print("No entries have drifted from their anchor.")
        return
    curated = load_curated()
    print(f"{len(stale)} entr{'y' if len(stale) == 1 else 'ies'} out of date:\n")
    for entry in stale:
        print(
            f"  {entry.name:<28} {entry.days:>5}d   "
            f"derived when {entry.anchor} was {entry.anchor_days}d, "
            f"now {curated[entry.anchor]}d"
        )
    print("\nReject these to have them re-derived on next use.")


def command_approve_anchored(store: LearnedShelfLifeStore) -> None:
    pending = [entry for entry in store.pending() if entry.is_anchored]
    if not pending:
        print("No anchored entries are pending.")
        return
    for entry in pending:
        promote(store, entry.name)
    print(f"\nPromoted {len(pending)} anchored entr{'y' if len(pending) == 1 else 'ies'}.")


def command_reject(store: LearnedShelfLifeStore, name: str) -> None:
    if store.forget(name):
        print(f"Removed {name!r}. It will be re-derived next time it is needed.")
    else:
        print(f"No learned entry named {name!r}.")


def command_correct(store: LearnedShelfLifeStore, name: str, days: int) -> None:
    """Replace a wrong value with a human one and promote it immediately.

    A correction is the most valuable input the system can receive, so it goes
    straight into the curated file rather than waiting for a separate approval.
    """
    store.remember(name, days=days, model="human-correction")
    curated = load_curated()
    curated[name] = days
    save_curated(curated)
    store.confirm(name)
    print(f"Corrected {name!r} to {days}d and promoted it into the curated file.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="include confirmed entries")
    parser.add_argument(
        "--stale", action="store_true", help="list entries whose anchor has changed"
    )
    parser.add_argument("--approve", metavar="NAME", help="promote one entry")
    parser.add_argument(
        "--approve-anchored",
        action="store_true",
        help="promote every pending entry derived from a known item",
    )
    parser.add_argument("--reject", metavar="NAME", help="delete one entry")
    parser.add_argument(
        "--correct",
        nargs=2,
        metavar=("NAME", "DAYS"),
        help="set a value by hand and promote it",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    store = LearnedShelfLifeStore()

    if args.approve:
        promote(store, args.approve)
    elif args.approve_anchored:
        command_approve_anchored(store)
    elif args.reject:
        command_reject(store, args.reject)
    elif args.correct:
        command_correct(store, args.correct[0], int(args.correct[1]))
    elif args.stale:
        command_stale(store)
    else:
        command_list(store, show_all=args.all)


if __name__ == "__main__":
    main()
