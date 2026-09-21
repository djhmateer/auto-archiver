# Tests for the serialized (JSON) log format in custom_logger.extract_log_data.
#
# loguru's raw `record["exception"]` holds type/value/traceback objects that json can't serialize
# so test here that our .join code in customer_logger.py works

import json

from auto_archiver.utils.custom_logger import logger


def _capture_serialized():
    """Adds a sink using the same serialized format as the file logs, returns (records, handler_id)."""
    records = []

    def sink(message):
        # loguru appends the raw traceback after the formatted line, the JSON is always the first line
        records.append(json.loads(str(message).splitlines()[0]))

    handler_id = logger.add(sink, format="{extra[serialized]}", level="DEBUG")
    return records, handler_id


def test_logging_an_exception_does_not_raise_and_includes_traceback():
    records, handler_id = _capture_serialized()
    try:
        try:
            1 / 0
        except ZeroDivisionError:
            logger.opt(exception=True).error("something failed")
    finally:
        logger.remove(handler_id)

    assert records[0]["message"] == "something failed"
    assert "ZeroDivisionError" in records[0]["exception"]


def test_logger_catch_logs_instead_of_masking_the_error():
    @logger.catch
    def failing():
        raise KeyError("the real error")

    records, handler_id = _capture_serialized()
    try:
        failing()  # must not raise (previously a TypeError from the log serializer)
    finally:
        logger.remove(handler_id)

    assert "KeyError" in records[0]["exception"]
    assert "the real error" in records[0]["exception"]


def test_serialized_log_without_exception_has_no_exception_key():
    records, handler_id = _capture_serialized()
    try:
        logger.warning("plain message")
    finally:
        logger.remove(handler_id)

    assert "exception" not in records[0]
