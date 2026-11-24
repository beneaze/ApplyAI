import logging
import sys

log = logging.getLogger("applyai")


def setup(verbose: bool = False, logfile=None):
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.DEBUG if verbose else logging.INFO)
    h.setFormatter(fmt)
    log.addHandler(h)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
