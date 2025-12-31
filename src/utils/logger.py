"""Enhanced logging utilities for debugging and monitoring."""

import logging
import sys
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
from functools import wraps
import traceback


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Add extra fields
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data

        return json.dumps(log_data)


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


class ContextLogger(logging.LoggerAdapter):
    """Logger adapter that adds context to all log messages."""

    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        # Add context prefix if available
        context_parts = []
        for key, value in self.extra.items():
            context_parts.append(f"{key}={value}")

        if context_parts:
            context_str = " ".join(context_parts)
            msg = f"[{context_str}] {msg}"

        return msg, kwargs


def setup_logger(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: bool = False,
    colored: bool = True,
) -> logging.Logger:
    """
    Setup logger with console and optional file output.

    Args:
        name: Logger name
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log output
        json_format: Use JSON format for file logging
        colored: Use colored output for console

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper()))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper()))

    # Console format (colored or plain)
    if colored and sys.stdout.isatty():
        console_formatter = ColoredFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        # Ensure log directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level.upper()))

        if json_format:
            file_handler.setFormatter(JSONFormatter())
        else:
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "[%(module)s:%(funcName)s:%(lineno)d] - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))

        logger.addHandler(file_handler)

    return logger


def get_logger(name: str, **context) -> ContextLogger:
    """
    Get a context-aware logger.

    Args:
        name: Logger name
        **context: Context key-value pairs to include in messages

    Returns:
        ContextLogger with context
    """
    base_logger = logging.getLogger(name)
    if not base_logger.handlers:
        setup_logger(name)
    return ContextLogger(base_logger, context)


def log_function_call(logger: logging.Logger = None):
    """
    Decorator to log function entry, exit, and exceptions.

    Usage:
        @log_function_call(logger)
        def my_function(arg1, arg2):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            nonlocal logger
            if logger is None:
                logger = logging.getLogger(func.__module__)

            func_name = func.__qualname__

            # Log entry
            logger.debug(
                f"ENTER {func_name} | args={args[:3]}... kwargs={list(kwargs.keys())}"
            )

            try:
                result = func(*args, **kwargs)
                logger.debug(f"EXIT {func_name} | success=True")
                return result

            except Exception as e:
                logger.error(
                    f"EXIT {func_name} | success=False | "
                    f"error={type(e).__name__}: {str(e)[:100]}"
                )
                raise

        return wrapper
    return decorator


def log_api_call(logger: logging.Logger):
    """
    Decorator specifically for API calls with timing and response logging.

    Usage:
        @log_api_call(logger)
        def api_method(self, endpoint, params):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time

            start_time = time.time()

            # Extract useful info from args/kwargs
            method_name = func.__name__

            logger.debug(f"API CALL START | method={method_name}")

            try:
                result = func(*args, **kwargs)
                elapsed = (time.time() - start_time) * 1000

                logger.debug(
                    f"API CALL SUCCESS | method={method_name} | "
                    f"elapsed_ms={elapsed:.2f}"
                )
                return result

            except Exception as e:
                elapsed = (time.time() - start_time) * 1000
                logger.error(
                    f"API CALL FAILED | method={method_name} | "
                    f"elapsed_ms={elapsed:.2f} | "
                    f"error={type(e).__name__}: {str(e)[:200]}"
                )
                raise

        return wrapper
    return decorator


class LogCapture:
    """Context manager to capture log output for testing."""

    def __init__(self, logger_name: str = None, level: int = logging.DEBUG):
        self.logger_name = logger_name
        self.level = level
        self.records = []
        self.handler = None

    def __enter__(self):
        self.handler = logging.Handler()
        self.handler.emit = lambda record: self.records.append(record)
        self.handler.setLevel(self.level)

        if self.logger_name:
            logger = logging.getLogger(self.logger_name)
        else:
            logger = logging.getLogger()

        logger.addHandler(self.handler)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.logger_name:
            logger = logging.getLogger(self.logger_name)
        else:
            logger = logging.getLogger()

        logger.removeHandler(self.handler)

    @property
    def messages(self) -> list:
        """Get all captured log messages."""
        return [record.getMessage() for record in self.records]

    def has_message(self, substring: str) -> bool:
        """Check if any message contains the substring."""
        return any(substring in msg for msg in self.messages)

    def get_errors(self) -> list:
        """Get all ERROR and CRITICAL level messages."""
        return [
            record.getMessage()
            for record in self.records
            if record.levelno >= logging.ERROR
        ]
