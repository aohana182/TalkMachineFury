"""
Native Messaging Host stub — v1.

In v1, users start the server manually:
    uvicorn server.main:app --port 8765

This file is a placeholder for a future native messaging host that would
allow the Chrome extension to auto-start the Python server.

v1 limitation: user must start server manually. The popup shows
"Start the server first" if /health is unreachable.

v1.1 plan (post-launch):
  - Register this script as a native messaging host
  - Extension sends 'start-server' message → host spawns uvicorn
  - Host monitors server health, restarts on crash
"""


def main():
    print("Talk Machine Fury — Native Messaging Host")
    print("v1: Start the server manually:")
    print("    cd /path/to/TalkMachineFury")
    print("    uvicorn server.main:app --port 8765")
    print()
    print("This script will become a full host in v1.1.")


if __name__ == "__main__":
    main()
