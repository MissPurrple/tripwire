"""Command line for Tripwire.

    python -m tripwire check     check everything, then save Internet Archive copies
    python -m tripwire summary   print everyone's weekly summary
"""

import sys

from .checker import Tripwire


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    tw = Tripwire()
    if command == "check":
        new = tw.check_all()
        print(f"Checked everything. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
        for row in tw.conn.execute("SELECT label, last_status FROM watches ORDER BY id").fetchall():
            print(f"  - {row['label']}: {row['last_status']}")
        print("Saving copies to the Internet Archive (each can take a minute)...")
        print(f"Done. {tw.save_archive_copies()} copies saved.")
    elif command == "summary":
        for user in tw.conn.execute("SELECT * FROM users ORDER BY id").fetchall():
            print(f"{user['email']}: {tw.weekly_summary(user['id'])['text']}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
