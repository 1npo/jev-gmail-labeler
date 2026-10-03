"""
Run on a schedule (daily is comfortable, given the 7-day expiration) to
keep the watch alive. See gmail-watch-renew.service + .timer for a
systemd-based schedule, or add a cron line -- see README.md.
"""

from watch import start_watch

if __name__ == "__main__":
    start_watch()
