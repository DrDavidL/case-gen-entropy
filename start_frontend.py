#!/usr/bin/env python3

import os
import subprocess
import sys

if __name__ == "__main__":
    os.chdir("frontend")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", "8501"],
        check=False,
    )
