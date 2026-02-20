"""Non-fatal logging setup. NEVER throws an exception."""
import logging
import logging.handlers
import sys
from pathlib import Path
from src.utils.paths import data_path


def setup_logging(name: str = "wallpaper_scraper", log_dir: str = "") -> logging.Logger:
    """Set up logging with file + console handlers. Falls back to console-only on any error."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — always works
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler — wrapped in try/except, never crashes
    try:
        if not log_dir:
            log_dir = str(data_path("logs"))
        if log_dir:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path / "scraper.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
    except Exception as e:
        logger.warning(f"Could not set up file logging: {e}. Using console only.")

    return logger
