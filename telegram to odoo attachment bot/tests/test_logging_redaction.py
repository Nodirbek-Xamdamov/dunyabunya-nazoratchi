import io
import logging
import logging.handlers

from docbot.logging_setup import RedactingFormatter, setup_logging
from docbot.redaction import MASK, redact, register_secrets

TG_TOKEN = "123456789:AAHdummyTokenForTestsOnly_0123456789abcd"
ODOO_KEY = "0123456789abcdef0123456789abcdef01234567"


def test_registered_secret_is_masked_everywhere():
    register_secrets([ODOO_KEY])
    assert redact(f"login failed for key {ODOO_KEY}!") == f"login failed for key {MASK}!"
    assert redact(f"{ODOO_KEY} and again {ODOO_KEY}") == f"{MASK} and again {MASK}"


def test_short_or_empty_values_are_not_registered():
    register_secrets(["", None, "abc"])  # masking "abc" would shred ordinary text
    assert redact("abc def") == "abc def"


def test_longer_secret_wins_over_its_own_prefix():
    register_secrets(["secret-value", "secret-value-extended"])
    assert redact("secret-value-extended") == MASK


def test_telegram_token_in_a_url_is_masked_without_registration():
    text = f"GET https://api.telegram.org/bot{TG_TOKEN}/getUpdates failed"
    assert TG_TOKEN not in redact(text)
    assert "api.telegram.org" in redact(text)


def test_file_download_url_is_masked():
    url = f"https://api.telegram.org/file/bot{TG_TOKEN}/photos/file_1.jpg"
    assert TG_TOKEN not in redact(url)


def test_openai_style_key_and_bearer_header_are_masked():
    assert "sk-proj-abcdefghijklmnop1234" not in redact("key sk-proj-abcdefghijklmnop1234 rejected")
    out = redact("Authorization: Bearer abcdef1234567890")
    assert "abcdef1234567890" not in out
    assert out.startswith("Authorization: Bearer ")


def test_ordinary_text_is_untouched():
    text = "job 42: received -> qr_ok (chat -1001, message 7) at 12:30:15"
    assert redact(text) == text


def _capture(formatter: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    logger = logging.getLogger("test.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def test_formatter_masks_message_arguments_and_tracebacks():
    register_secrets([ODOO_KEY])
    logger, stream = _capture(RedactingFormatter("%(message)s"))
    try:
        raise RuntimeError(f"boom with {ODOO_KEY} in https://x/bot{TG_TOKEN}/y")
    except RuntimeError:
        logger.error("request for %s failed", ODOO_KEY, exc_info=True)
    output = stream.getvalue()
    assert ODOO_KEY not in output
    assert TG_TOKEN not in output
    assert "RuntimeError" in output  # the traceback itself is still there


def test_setup_logging_writes_a_masked_file_and_is_idempotent(tmp_path):
    register_secrets([ODOO_KEY])
    for _ in range(2):
        setup_logging("INFO", log_dir=tmp_path / "logs")
    root = logging.getLogger()
    assert sum(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers) == 1

    logging.getLogger("docbot.test").info("using key %s", ODOO_KEY)
    for handler in root.handlers:
        handler.flush()
    content = (tmp_path / "logs" / "docbot.log").read_text(encoding="utf-8")
    assert "using key" in content
    assert ODOO_KEY not in content
    assert MASK in content


def test_setup_logging_without_directory_logs_to_console_only(tmp_path):
    setup_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
