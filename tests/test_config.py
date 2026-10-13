import shutil
from pathlib import Path

import pytest
from helpers import VALID_DOCUMENTS, VALID_ENV

from docbot.config import (
    EnvSettings,
    DocumentsConfig,
    find_placeholders,
    load_config,
    readiness_issues,
    resolve_home,
)
from docbot.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """Real environment variables override .env; make sure the developer's shell cannot leak in."""
    for name in EnvSettings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.delenv("DOCBOT_HOME", raising=False)


@pytest.fixture
def home(tmp_path) -> Path:
    shutil.copytree(PROJECT_ROOT / "config", tmp_path / "config")
    (tmp_path / ".env").write_text(VALID_ENV, encoding="utf-8")
    return tmp_path


def write_documents(home: Path, text: str) -> None:
    (home / "config" / "documents.yaml").write_text(text, encoding="utf-8")


def with_openai_key(home: Path) -> None:
    (home / ".env").write_text(VALID_ENV + "OPENAI_API_KEY=sk-test-0123456789abcdefghij\n", encoding="utf-8")


def problems_of(home: Path) -> list[str]:
    with pytest.raises(ConfigError) as info:
        load_config(home)
    return info.value.problems


# ----------------------------------------------------------------------------- happy path


def test_shipped_templates_are_valid_configs(home):
    cfg = load_config(home)
    assert cfg.settings.queue.max_attempts == 20
    assert [kind.key for kind in cfg.documents.documents] == ["sale", "purchase", "picking"]
    assert cfg.settings.ai.model == "gpt-5.4-mini"


def test_env_values_are_parsed(home):
    cfg = load_config(home)
    assert cfg.env.allowed_chat_ids == [-1001111111111, -1002222222222]
    assert cfg.env.admin_ids == [111, 222]
    assert cfg.env.odoo_url == "https://odoo.example.com"  # trailing slash dropped
    assert cfg.env.bot_token.get_secret_value().startswith("123456789:")
    assert cfg.env.openai_api_key is None


def test_default_paths_live_under_home(home):
    cfg = load_config(home)
    assert cfg.data_dir == home / "data"
    assert cfg.images_dir == home / "data" / "images"
    assert cfg.database_url.startswith("sqlite+aiosqlite:///")
    assert cfg.database_url.endswith("data/docbot.db")


def test_data_dir_and_database_url_can_be_overridden(home):
    (home / ".env").write_text(VALID_ENV + "DATA_DIR=custom\nDATABASE_URL=postgresql+asyncpg://u:pw123456@h/db\n", encoding="utf-8")
    cfg = load_config(home)
    assert cfg.data_dir == home / "custom"
    assert cfg.database_url == "postgresql+asyncpg://u:pw123456@h/db"
    assert "pw123456" in cfg.secret_values()  # the DB password is masked in logs too


def test_blank_optional_values_mean_unset(home):
    (home / ".env").write_text(VALID_ENV + "OPENAI_API_KEY=\nDATABASE_URL=\n", encoding="utf-8")
    cfg = load_config(home)
    assert cfg.env.openai_api_key is None
    assert cfg.env.database_url is None


def test_real_environment_overrides_dotenv(home, monkeypatch):
    monkeypatch.setenv("ODOO_DB", "from-environment")
    assert load_config(home).env.odoo_db == "from-environment"


# ----------------------------------------------------------------------------- placeholders / readiness


def test_templates_are_loadable_but_not_ready(home):
    shutil.copy(PROJECT_ROOT / ".env.example", home / ".env")
    cfg = load_config(home)
    found = find_placeholders(cfg)
    assert ".env: BOT_TOKEN" in found
    assert ".env: ODOO_API_KEY" in found
    assert ".env: OPENAI_API_KEY" in found
    issues = readiness_issues(cfg)
    assert any("ALLOWED_CHAT_IDS" in i for i in issues)
    assert any("ADMIN_IDS" in i for i in issues)


def test_ai_needs_a_key_unless_it_is_switched_off(home):
    issues = readiness_issues(load_config(home))
    assert issues == [".env: OPENAI_API_KEY bo'sh, lekin settings.yaml'da ai.enabled: true"]
    settings = home / "config" / "settings.yaml"
    settings.write_text(settings.read_text(encoding="utf-8").replace("  enabled: true", "  enabled: false"), encoding="utf-8")
    assert readiness_issues(load_config(home)) == []


def test_filled_env_and_shipped_documents_are_ready(home):
    with_openai_key(home)
    assert find_placeholders(load_config(home)) == []
    assert readiness_issues(load_config(home)) == []


def test_placeholder_report_never_contains_values(home):
    shutil.copy(PROJECT_ROOT / ".env.example", home / ".env")
    text = "\n".join(readiness_issues(load_config(home)))
    assert "123456789" not in text


# ----------------------------------------------------------------------------- errors


def test_missing_env_file_and_variables_are_reported_together(home):
    (home / ".env").unlink()
    problems = problems_of(home)
    text = "\n".join(problems)
    assert ".env fayli topilmadi" in text
    for name in ("BOT_TOKEN", "ODOO_URL", "ODOO_DB", "ODOO_USER", "ODOO_API_KEY"):
        assert name in text


def test_errors_from_different_files_are_collected(home):
    (home / ".env").write_text(VALID_ENV.replace("ODOO_DB=prod", ""), encoding="utf-8")
    (home / "config" / "settings.yaml").write_text("queue:\n  workers: 0\n", encoding="utf-8")
    write_documents(home, "documents: []\n")
    text = "\n".join(problems_of(home))
    assert "ODOO_DB" in text
    assert "settings.yaml: queue.workers" in text
    assert "documents.yaml:" in text


def test_bad_chat_id_list_is_rejected_without_echoing_it(home):
    (home / ".env").write_text(VALID_ENV.replace("-1001111111111, -1002222222222", "abc-secret"), encoding="utf-8")
    text = "\n".join(problems_of(home))
    assert "ALLOWED_CHAT_IDS" in text
    assert "abc-secret" not in text


def test_secret_values_never_appear_in_error_text(home):
    env = VALID_ENV.replace("https://odoo.example.com/", "ftp://leaky-host.example")
    (home / ".env").write_text(env, encoding="utf-8")
    text = "\n".join(problems_of(home))
    assert "ODOO_URL" in text
    assert "leaky-host" not in text
    assert "0123456789abcdef" not in text
    assert "AAHdummyToken" not in text


def test_unknown_yaml_key_is_an_error(home):
    (home / "config" / "settings.yaml").write_text("queue:\n  max_attemps: 3\n", encoding="utf-8")
    text = "\n".join(problems_of(home))
    assert "settings.yaml: queue.max_attemps" in text


def test_backoff_max_below_base_is_rejected(home):
    (home / "config" / "settings.yaml").write_text(
        "queue:\n  backoff_base_seconds: 60\n  backoff_max_seconds: 10\n", encoding="utf-8"
    )
    assert any("backoff_max_seconds" in p for p in problems_of(home))


def test_broken_yaml_is_reported_with_line(home):
    (home / "config" / "settings.yaml").write_text("queue:\n  workers: [1, 2\n", encoding="utf-8")
    assert any("settings.yaml: YAML xato" in p for p in problems_of(home))


def test_missing_config_file_is_reported(home):
    (home / "config" / "documents.yaml").unlink()
    assert any("documents.yaml: fayl topilmadi" in p for p in problems_of(home))


def test_log_level_is_validated_and_normalised(home):
    (home / "config" / "settings.yaml").write_text("logging:\n  level: debug\n", encoding="utf-8")
    assert load_config(home).settings.logging.level == "DEBUG"
    (home / "config" / "settings.yaml").write_text("logging:\n  level: loud\n", encoding="utf-8")
    assert any("logging.level" in p for p in problems_of(home))


# ----------------------------------------------------------------------------- documents.yaml rules


def document_problems(home: Path, text: str) -> str:
    write_documents(home, text)
    return "\n".join(problems_of(home))


def test_a_small_document_list_is_accepted(home):
    write_documents(home, VALID_DOCUMENTS)
    kinds = load_config(home).documents.documents
    assert [(k.key, k.model, k.total_field, k.token_field) for k in kinds] == [("sale", "sale.order", "amount_total", "access_token")]


def test_code_patterns_need_an_id_or_name_group(home):
    text = document_problems(home, VALID_DOCUMENTS.replace("'^(?P<name>S\\d{5,})$'", "'^S\\d{5,}$'"))
    assert "'id' yoki 'name' nomli guruh kerak" in text


def test_a_token_group_needs_an_id_group(home):
    text = document_problems(home, VALID_DOCUMENTS.replace("(?P<id>\\d+)", "(?P<name>\\d+)"))
    assert "'token' faqat 'id' bilan" in text


def test_broken_regexes_are_reported(home):
    assert "regex xato" in document_problems(home, VALID_DOCUMENTS.replace("number: 'S\\d{5,}'", "number: '(S\\d{5,}'"))
    assert "regex xato" in document_problems(home, VALID_DOCUMENTS.replace("'^(?P<name>S\\d{5,})$'", "'^(?P<name>S[)$'"))


def test_document_keys_are_unique(home):
    body = VALID_DOCUMENTS.split("documents:\n", 1)[1]
    assert "takrorlangan key" in document_problems(home, VALID_DOCUMENTS + body)


def test_model_names_are_validated(home):
    assert "documents.0.model" in document_problems(home, VALID_DOCUMENTS.replace("model: sale.order", "model: Sale Order"))


def test_at_least_one_document_kind_is_needed():
    with pytest.raises(ValueError):
        DocumentsConfig.model_validate({"documents": []})


def test_ai_and_file_settings_are_validated(home):
    (home / "config" / "settings.yaml").write_text(
        "ai:\n  reasoning_effort: extreme\nfiles:\n  max_mb: 50\n", encoding="utf-8"
    )
    text = "\n".join(problems_of(home))
    assert "settings.yaml: ai.reasoning_effort" in text
    assert "settings.yaml: files.max_mb" in text  # Telegram bots cannot download more than 20 MB


# ----------------------------------------------------------------------------- home resolution


def test_resolve_home_prefers_explicit_then_env_var(home, tmp_path_factory, monkeypatch):
    other = tmp_path_factory.mktemp("other")
    (other / "config").mkdir()
    monkeypatch.setenv("DOCBOT_HOME", str(other))
    assert resolve_home(home) == home.resolve()
    assert resolve_home(None) == other.resolve()


def test_resolve_home_rejects_folder_without_config(tmp_path):
    with pytest.raises(ConfigError, match="config"):
        resolve_home(tmp_path)


def test_resolve_home_falls_back_to_the_checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no config/ here
    assert resolve_home(None) == PROJECT_ROOT.resolve()


def test_a_missing_pages_text_with_an_unknown_placeholder_is_rejected(home):
    (home / "config" / "settings.yaml").write_text("notify:\n  missing_pages_text: \"{doc} yo'q\"\n", encoding="utf-8")
    assert any("missing_pages_text" in p for p in problems_of(home))
