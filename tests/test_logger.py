"""Unit tests for logging utilities."""

import os
import json
import logging
import tempfile
import pytest

from src.utils.logger import (
    setup_logger,
    get_logger,
    log_function_call,
    log_api_call,
    LogCapture,
    JSONFormatter,
    ColoredFormatter,
    ContextLogger,
)


class TestSetupLogger:
    """Tests for setup_logger function."""

    def test_creates_logger(self):
        """Test that setup_logger creates a logger."""
        logger = setup_logger("test_logger_1")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_logger_1"

    def test_sets_log_level(self):
        """Test that log level is set correctly."""
        logger = setup_logger("test_logger_2", level="DEBUG")
        assert logger.level == logging.DEBUG

        logger2 = setup_logger("test_logger_3", level="WARNING")
        assert logger2.level == logging.WARNING

    def test_adds_console_handler(self):
        """Test that console handler is added."""
        logger = setup_logger("test_logger_4")

        console_handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
        ]
        assert len(console_handlers) >= 1

    def test_file_handler(self):
        """Test that file handler is added when specified."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            logger = setup_logger("test_logger_5", log_file=log_path)

            file_handlers = [
                h for h in logger.handlers
                if isinstance(h, logging.FileHandler)
            ]
            assert len(file_handlers) >= 1

            # Test writing to file
            logger.info("Test message")

            with open(log_path, "r") as f:
                content = f.read()
            assert "Test message" in content

        finally:
            os.unlink(log_path)

    def test_json_format_file(self):
        """Test JSON formatting for file output."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name

        try:
            logger = setup_logger(
                "test_logger_6",
                log_file=log_path,
                json_format=True,
            )

            logger.info("JSON test message")

            # Force flush
            for handler in logger.handlers:
                handler.flush()

            with open(log_path, "r") as f:
                content = f.read().strip()

            # Should be valid JSON
            if content:
                log_entry = json.loads(content)
                assert log_entry["message"] == "JSON test message"
                assert "timestamp" in log_entry

        finally:
            os.unlink(log_path)

    def test_avoids_duplicate_handlers(self):
        """Test that calling setup_logger twice doesn't add duplicate handlers."""
        logger1 = setup_logger("test_logger_7")
        handler_count = len(logger1.handlers)

        logger2 = setup_logger("test_logger_7")
        assert len(logger2.handlers) == handler_count


class TestContextLogger:
    """Tests for ContextLogger class."""

    def test_adds_context_to_messages(self):
        """Test that context is added to log messages."""
        base_logger = logging.getLogger("test_context_1")
        base_logger.setLevel(logging.DEBUG)

        context_logger = ContextLogger(base_logger, {"user": "test", "request_id": "123"})

        with LogCapture("test_context_1") as capture:
            context_logger.info("Test message")

        assert capture.has_message("user=test")
        assert capture.has_message("request_id=123")


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_context_logger(self):
        """Test that get_logger returns a ContextLogger."""
        logger = get_logger("test_get_1", request_id="abc")

        assert isinstance(logger, ContextLogger)

    def test_context_in_messages(self):
        """Test that context appears in messages."""
        logger = get_logger("test_get_2", transaction="tx123")

        with LogCapture("test_get_2") as capture:
            logger.info("Processing")

        assert capture.has_message("transaction=tx123")


class TestLogFunctionCall:
    """Tests for log_function_call decorator."""

    def test_logs_function_entry_exit(self):
        """Test that decorator logs function entry and exit."""
        test_logger = setup_logger("test_func_1", level="DEBUG")

        @log_function_call(test_logger)
        def sample_function(x, y):
            return x + y

        with LogCapture("test_func_1", level=logging.DEBUG) as capture:
            result = sample_function(1, 2)

        assert result == 3
        assert capture.has_message("ENTER")
        assert capture.has_message("EXIT")
        assert capture.has_message("success=True")

    def test_logs_exceptions(self):
        """Test that decorator logs exceptions."""
        test_logger = setup_logger("test_func_2", level="DEBUG")

        @log_function_call(test_logger)
        def failing_function():
            raise ValueError("Test error")

        with LogCapture("test_func_2") as capture:
            with pytest.raises(ValueError):
                failing_function()

        assert capture.has_message("success=False")
        assert capture.has_message("ValueError")


class TestLogAPICall:
    """Tests for log_api_call decorator."""

    def test_logs_api_timing(self):
        """Test that decorator logs API call timing."""
        test_logger = setup_logger("test_api_1", level="DEBUG")

        @log_api_call(test_logger)
        def api_method():
            return {"data": "test"}

        with LogCapture("test_api_1", level=logging.DEBUG) as capture:
            result = api_method()

        assert result == {"data": "test"}
        assert capture.has_message("API CALL")
        assert capture.has_message("elapsed_ms")

    def test_logs_api_failures(self):
        """Test that decorator logs API failures."""
        test_logger = setup_logger("test_api_2", level="DEBUG")

        @log_api_call(test_logger)
        def failing_api():
            raise Exception("API Error")

        with LogCapture("test_api_2") as capture:
            with pytest.raises(Exception):
                failing_api()

        assert capture.has_message("API CALL FAILED")


class TestLogCapture:
    """Tests for LogCapture context manager."""

    def test_captures_messages(self):
        """Test that LogCapture captures log messages."""
        logger = logging.getLogger("test_capture_1")
        logger.setLevel(logging.DEBUG)

        with LogCapture("test_capture_1") as capture:
            logger.info("First message")
            logger.warning("Second message")

        assert len(capture.messages) == 2
        assert "First message" in capture.messages
        assert "Second message" in capture.messages

    def test_has_message_check(self):
        """Test has_message method."""
        logger = logging.getLogger("test_capture_2")
        logger.setLevel(logging.DEBUG)

        with LogCapture("test_capture_2") as capture:
            logger.info("Contains keyword foo")

        assert capture.has_message("foo")
        assert not capture.has_message("bar")

    def test_get_errors(self):
        """Test get_errors method."""
        logger = logging.getLogger("test_capture_3")
        logger.setLevel(logging.DEBUG)

        with LogCapture("test_capture_3") as capture:
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")
            logger.critical("Critical message")

        errors = capture.get_errors()
        assert len(errors) == 2
        assert "Error message" in errors
        assert "Critical message" in errors

    def test_cleanup_after_exit(self):
        """Test that handler is removed after context exit."""
        logger = logging.getLogger("test_capture_4")
        initial_handlers = len(logger.handlers)

        with LogCapture("test_capture_4"):
            pass

        assert len(logger.handlers) == initial_handlers


class TestJSONFormatter:
    """Tests for JSONFormatter class."""

    def test_formats_as_json(self):
        """Test that formatter produces valid JSON."""
        formatter = JSONFormatter()

        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert data["message"] == "Test message"
        assert data["level"] == "INFO"
        assert "timestamp" in data

    def test_includes_exception_info(self):
        """Test that exception info is included."""
        formatter = JSONFormatter()

        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=10,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        output = formatter.format(record)
        data = json.loads(output)

        assert "exception" in data
        assert data["exception"]["type"] == "ValueError"


class TestColoredFormatter:
    """Tests for ColoredFormatter class."""

    def test_adds_color_codes(self):
        """Test that color codes are added."""
        formatter = ColoredFormatter("%(levelname)s - %(message)s")

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=10,
            msg="Error message",
            args=(),
            exc_info=None,
        )

        output = formatter.format(record)

        # Should contain ANSI escape codes
        assert "\033[" in output
