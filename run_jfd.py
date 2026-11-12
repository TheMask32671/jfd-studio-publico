"""Inicia o JFD – Intelligent Audio Studio."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from jfd.app import main

    sys.exit(main())
