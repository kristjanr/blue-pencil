#!/usr/bin/env bash
# Back up the two things git cannot hold: the story graph and the corpus.
#
#   ./backup.sh            snapshot the graph, mirror the corpus
#   ./backup.sh --list     show what is already on the remote
#
# The graph is 30-odd MB of SQLite that cost real money to build, and the
# corpus is the source it was built from — including the book 6 transcript,
# which exists nowhere else. Both are gitignored on purpose: the books are
# not ours to publish, and a 34MB binary does not belong in a repo.
#
# Snapshots are DATED and never overwritten. A plain `rclone sync` is not a
# backup: corrupt the graph and it would faithfully replicate the corruption
# over the only good copy. Every snapshot here is integrity-checked before it
# is allowed off the machine.
set -uo pipefail
cd "$(dirname "$0")"

REMOTE="gdrive:bookz/blue-pencil"
DB="graph/bobiverse.sqlite"
KEEP=8                       # how many graph snapshots to retain

if [ "${1:-}" = "--list" ]; then
  echo "== graph snapshots =="; rclone lsl "$REMOTE/graph" 2>/dev/null | sort -k4
  echo; echo "== corpus =="; rclone lsl "$REMOTE/corpus" 2>/dev/null
  exit 0
fi

command -v rclone   >/dev/null || { echo "rclone not found" >&2; exit 1; }
command -v sqlite3  >/dev/null || { echo "sqlite3 not found" >&2; exit 1; }
[ -f "$DB" ] || { echo "no graph at $DB" >&2; exit 1; }

stamp=$(date +%Y%m%d-%H%M)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
snap="$tmp/bobiverse-$stamp.sqlite"

# VACUUM INTO takes a consistent snapshot of a live database — safe to run
# while something else is mid-extract, which a plain `cp` is not.
echo "snapshotting $DB ..."
sqlite3 "$DB" "VACUUM INTO '$snap'" || { echo "snapshot failed" >&2; exit 1; }

echo "checking integrity ..."
check=$(sqlite3 "$snap" "PRAGMA integrity_check;" | head -1)
[ "$check" = "ok" ] || { echo "REFUSING TO UPLOAD: integrity_check says '$check'" >&2; exit 1; }

# A manifest makes snapshots tellable apart without downloading 11MB to look.
{
  echo "taken:    $(date -Iseconds)"
  echo "host:     $(hostname)"
  echo "commit:   $(git rev-parse --short HEAD 2>/dev/null || echo '-')"
  echo "db bytes: $(stat -c%s "$snap")"
  for t in scenes entities events objects promises threads citations contradictions exemplars; do
    n=$(sqlite3 "$snap" "SELECT COUNT(*) FROM $t;" 2>/dev/null || echo "-")
    printf '%-15s %s\n' "$t:" "$n"
  done
  echo "scenes cited: $(sqlite3 "$snap" 'SELECT COUNT(DISTINCT scene_id) FROM citations;' 2>/dev/null)"
} > "$tmp/bobiverse-$stamp.txt"

gzip -6 "$snap"
echo "uploading $(du -h "$snap.gz" | cut -f1) ..."
rclone copy "$snap.gz"            "$REMOTE/graph" --progress || exit 1
rclone copy "$tmp/bobiverse-$stamp.txt" "$REMOTE/graph" || exit 1

# The corpus barely changes — copy, never sync, so nothing up there is ever
# deleted by something going missing down here.
echo "mirroring corpus ..."
rclone copy corpus "$REMOTE/corpus" --exclude ".gitkeep" --progress

# Retention: keep the newest $KEEP snapshots, drop the rest.
mapfile -t old < <(rclone lsf "$REMOTE/graph" --include "*.sqlite.gz" | sort | head -n "-$KEEP")
for f in "${old[@]:-}"; do
  [ -n "$f" ] || continue
  echo "pruning $f"
  rclone deletefile "$REMOTE/graph/$f"
  rclone deletefile "$REMOTE/graph/${f%.sqlite.gz}.txt" 2>/dev/null
done

echo
echo "backed up to $REMOTE"
rclone lsf "$REMOTE/graph" --include "*.sqlite.gz" | sort | tail -3
