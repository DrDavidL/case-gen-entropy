#!/usr/bin/env python3

import subprocess
import sys
import os

if __name__ == "__main__":
    os.chdir("frontend")
    subprocess.run([sys.executable, "-m", "streamlit", "run", "app.py", "--server.port", "8501"])