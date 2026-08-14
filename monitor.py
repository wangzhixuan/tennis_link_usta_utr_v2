"""Monitor batch_fetch progress. Run in a separate terminal: python monitor.py"""
import time
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tennislink.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "batch_fetch.log")

def get_counts():
    c = sqlite3.connect(DB_PATH)
    usta = c.execute("SELECT COUNT(*) FROM usta_player_profiles").fetchone()[0]
    utr = c.execute("SELECT COUNT(*) FROM utr_player_profiles").fetchone()[0]
    c.close()
    return usta, utr

def get_last_log():
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
            return lines[-5:] if lines else ["(no log yet)"]
    except FileNotFoundError:
        return ["(log not found)"]

def main():
    print("Monitoring batch_fetch every 10min. Ctrl+C to stop.\n")
    while True:
        usta, utr = get_counts()
        tail = get_last_log()
        print(f"[{time.strftime('%H:%M:%S')}] USTA: {usta} | UTR: {utr}")
        for line in tail:
            print(f"  {line.rstrip()}")
        print()
        time.sleep(600)

if __name__ == "__main__":
    main()
