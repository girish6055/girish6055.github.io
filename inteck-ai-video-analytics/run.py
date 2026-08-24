#!/usr/bin/env python3
"""INTECK AI Video Analytics - launcher (also the PyInstaller entry point)."""
import multiprocessing
import sys

from inteck.main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for a frozen Windows build
    sys.exit(main())
