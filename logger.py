import logging
import sys
from logging.handlers import RotatingFileHandler

class ColorFormatter(logging.Formatter):
    """Custom formatter to add colors to the console output"""
    
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    blue = "\x1b[34;20m"
    reset = "\x1b[0m"
    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format_str + reset,
        logging.INFO: blue + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)

def setup_logger(name="feishu_bot"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Console Handler with ColorFormatter
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(ColorFormatter())

    # File Handler with Rotating logic (10MB max per file, keep 5 backups)
    fh = RotatingFileHandler("feishu_bot.log", maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
    fh.setLevel(logging.INFO)
    file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)", datefmt='%Y-%m-%d %H:%M:%S')
    fh.setFormatter(file_formatter)

    # Add handlers
    if not logger.handlers:
        logger.addHandler(ch)
        logger.addHandler(fh)

    return logger

log = setup_logger()
