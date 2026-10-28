"""Command line for Tripwire.

    python -m tripwire check     check everything you're watching, right now
    python -m tripwire summary   print the weekly summary
"""

import sys

from .checker import Tripwire


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    tw = Tripwire()
    if command == "check":
        new = tw.check_all()
        print(f"Checked everything. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
        for row in tw.conn.execute("SELECT label, last_status FROM watches ORDER BY id"):
            print(f"  - {row['label']}: {row['last_status']}")
        print("Saving copies to the Internet Archive (this can take a minute)...")
        tw.wait_for_archive()
        print("Done.")
    elif command == "summary":
        print(tw.weekly_summary()["text"])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
