import subprocess
import sys

subprocess.Popen(
    [sys.executable, "batch_fetch.py", "--usta", "--utr"],
    cwd=r"C:\Users\cicic\Projects\TennisLink",
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
    close_fds=True
)
