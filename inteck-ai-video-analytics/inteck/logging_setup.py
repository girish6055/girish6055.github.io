import logging
import logging.handlers
import sys


def setup_logging(log_file: str = "logs/inteck.log", level: str = "INFO") -> logging.Logger:
    from .paths import resolve

    path = resolve(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)-24s | %(message)s")

    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=8 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("ultralytics").setLevel(logging.WARNING)
    return root
