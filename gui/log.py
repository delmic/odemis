
import logging

log = None
_level = logging.DEBUG


def get_logger():
    logging.basicConfig(format=" - %(levelname)s \t%(message)s")
    l = logging.getLogger()
    l.setLevel(_level)
    l.handlers[0].setFormatter(
      logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))

    return l

if log is None:
    log = get_logger()