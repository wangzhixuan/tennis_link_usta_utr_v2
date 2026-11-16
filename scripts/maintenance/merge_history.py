"""Merge checked_events so each event has a single (latest) row.

Groups by tournament + normalized event label. If exactly one full event ID is present
in a group, the merged row adopts that event ID (canonical key). Keeps the most recently
checked roster. Ambiguous groups (multiple distinct event IDs) are left untouched.

Run (from repo root):
    python -m scripts.maintenance.merge_history [--dry-run]
"""
import argparse
import logging

from scripts.service import db

logger = logging.getLogger("merge_history")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def merge_history(dry_run=False):
    rows = db.get_checked_events()
    groups = {}
    for r in rows:
        key = (r.get("tournament_id"),
               db.normalize_event_label(r.get("event_label")))
        groups.setdefault(key, []).append(r)

    removed = 0
    rekeyed = 0
    skipped = 0
    for key, group in groups.items():
        event_ids = {r.get("event_id") for r in group if r.get("event_id")}
        if len(event_ids) > 1:
            logger.info(f"skip ambiguous group {key}: event ids {event_ids}")
            skipped += 1
            continue

        group.sort(key=lambda r: r.get("last_checked") or "")
        keep = group[-1]
        event_id = next(iter(event_ids)) if event_ids else None

        if event_id:
            canonical = f"{keep['tournament_id']}:{event_id}"
        else:
            canonical = f"{keep['tournament_id']}:division:{keep.get('event_label')}"

        if len(group) > 1:
            logger.info(
                f"merge {len(group)} -> 1: {keep.get('tournament_name')} — "
                f"{keep.get('event_label')} (keep {keep.get('last_checked')})"
            )
        group_removed = max(0, len(group) - 1)
        if dry_run:
            removed += group_removed
            continue

        if keep.get("event_key") != canonical:
            db.save_checked_event(
                canonical, keep.get("tournament_id"), event_id,
                keep.get("tournament_name"), keep.get("event_label"),
                keep.get("level"), keep.get("division"), keep.get("roster") or [],
                event_date=keep.get("event_date"), checked_at=keep.get("last_checked"),
            )
            for r in group:
                if r.get("event_key") != canonical:
                    db.delete_checked_event(r.get("event_key"))
            rekeyed += 1
        else:
            # keep is canonical; drop the rest
            for r in group:
                if r.get("event_key") != canonical:
                    db.delete_checked_event(r.get("event_key"))

        if len(group) > 1:
            removed += len(group) - 1

    result = {"before": len(rows), "after": len(rows) - removed,
              "removed": removed, "rekeyed": rekeyed, "skipped": skipped}
    logger.info(f"Merge complete. {result}")
    return result


def main():
    p = argparse.ArgumentParser(description="Merge checked_events to one row per event.")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    merge_history(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
