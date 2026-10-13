"""Application configuration.

Three sources, kept apart on purpose:

* ``.env`` / process environment  - secrets and deployment specifics (tokens, Odoo credentials, chat ids)
* ``config/settings.yaml``        - behaviour knobs (queue, files, codes, AI, Odoo, notifications, logging)
* ``config/documents.yaml``       - document kinds: Odoo model, QR/barcode patterns, number shape

Every problem found is collected and reported together (see ``ConfigError``); error texts never
contain values, so a bad secret cannot leak through a validation message.
"""

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from docbot.errors import ConfigError

PLACEHOLDER = "REPLACE_ME"
HOME_ENV_VAR = "DOCBOT_HOME"

_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class _Strict(BaseModel):
    """Unknown keys are errors: a typo in a YAML file must not silently fall back to a default."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- settings.yaml


class QueueSettings(_Strict):
    workers: int = Field(2, ge=1, le=32)
    poll_interval_seconds: float = Field(2.0, gt=0)
    # A claimed job is locked for this long; if the worker dies the lock expires and the job is recovered.
    lease_seconds: float = Field(300.0, gt=0)
    # Consecutive failures of one step before the job becomes `failed` (attempts reset after every successful step).
    max_attempts: int = Field(20, ge=1)
    backoff_base_seconds: float = Field(30.0, gt=0)
    backoff_max_seconds: float = Field(900.0, gt=0)
    backoff_jitter: float = Field(0.1, ge=0.0, lt=1.0)

    @model_validator(mode="after")
    def _backoff_order(self) -> Self:
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")
        return self


class LoggingSettings(_Strict):
    level: str = "INFO"
    to_file: bool = True
    max_bytes: int = Field(10 * 1024 * 1024, ge=1024)
    backup_count: int = Field(10, ge=1)

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        value = value.upper()
        if value not in _LOG_LEVELS:
            raise ValueError(f"must be one of {sorted(_LOG_LEVELS)}")
        return value


class FilesSettings(_Strict):
    # The Bot API refuses to hand out files above 20 MB, so a larger limit could never work.
    max_mb: float = Field(20.0, gt=0, le=20)
    # Local copies of finished jobs are deleted after this many days (Odoo keeps the attached file).
    keep_days: int = Field(30, ge=1)


class CodesSettings(_Strict):
    pdf_pages: int = Field(3, ge=1, le=20)  # how many PDF pages are scanned for codes and numbers
    pdf_scale: float = Field(2.0, gt=0, le=6)  # render scale (1.0 = 72 dpi)


class AiSettings(_Strict):
    enabled: bool = True
    model: str = Field("gpt-5.4-mini", min_length=1)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] = "low"
    image_detail: Literal["low", "high", "auto"] = "high"
    max_image_px: int = Field(2048, ge=512, le=4096)  # longer side of the image sent to the API
    timeout_seconds: float = Field(90.0, gt=0)
    daily_limit: int = Field(1000, ge=1)  # AI calls per UTC day; above it unreadable pictures are dropped
    # A picture without our QR counts as one of our documents from this AI similarity (0-100) on; below it is ignored.
    min_similarity: int = Field(70, ge=0, le=100)
    # Nothing but the number was readable: trust it (the record exists) from this similarity on, logo seen.
    trust_number_from: int = Field(90, ge=0, le=100)


class OdooSettings(_Strict):
    timeout_seconds: float = Field(60.0, gt=0)


class NotifySettings(_Strict):
    # The file's message always gets a reaction (👀 👍 👌 🤔 👎). Text is opt-in:
    # questions with buttons, duplicate/failure notes and answers to typed numbers.
    messages: bool = False
    # With messages on: also a short text when a file was attached.
    success_message: bool = False
    # Reply to a file the bot could not place (QR unreadable and the AI unsure), in its own chat, also in groups.
    # Empty: only the 🤔 reaction.
    unclear_text: str = "📷 Hujjat aniqlanmadi. Rasmni boshqatdan tiniqroq qilib tashlang (QR kod aniq ko'rinsin)."
    # Reply to the last page (the QR page) of a multi-page document when a page before it was not sent:
    # {document} = its number, {missing} = e.g. "3-bet (41–60-qatorlar)". Empty: no such check.
    missing_pages_text: str = "⚠️ {document} hujjatida sahifa yetishmayapti: {missing}. Shu sahifani ham tiniq qilib tashlang."

    @field_validator("missing_pages_text")
    @classmethod
    def _missing_pages_text(cls, value: str) -> str:
        try:
            value.format(document="S1", missing="2-bet")
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"faqat {{document}} va {{missing}} ishlatiladi ({exc})") from None
        return value


class PagesSettings(_Strict):
    """Documents of several pages, photographed page by page (an album, or photos one after another)."""

    # Photos of one person in one chat within this many seconds may be pages of one document.
    window_seconds: float = Field(120.0, gt=0)
    # An album's photos arrive within a second or two: a page waits this long for its album mates.
    album_grace_seconds: float = Field(10.0, ge=0)
    # How often a page waiting for the other pages looks again.
    retry_seconds: float = Field(5.0, gt=0)


class AppSettings(_Strict):
    queue: QueueSettings = QueueSettings()
    files: FilesSettings = FilesSettings()
    codes: CodesSettings = CodesSettings()
    ai: AiSettings = AiSettings()
    odoo: OdooSettings = OdooSettings()
    notify: NotifySettings = NotifySettings()
    pages: PagesSettings = PagesSettings()
    logging: LoggingSettings = LoggingSettings()


# --------------------------------------------------------------------------- documents.yaml


class DocumentType(_Strict):
    """One kind of printed document and the Odoo model its records live in.

    ``codes`` are regexes for the text of a QR code / barcode. Named groups say how to find the record:
    ``id`` (+ optional ``token``, compared with ``token_field``) or ``name``. ``number`` is the shape of
    the document number as printed (AI output and numbers typed by people are routed with it).
    """

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    title: str = Field(min_length=1)  # shown to people ("Sotuv buyurtmasi")
    model: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
    number: str = Field(min_length=1)
    codes: list[str] = Field(default_factory=list)
    hint: str | None = None  # how the document looks, for the AI prompt
    token_field: str = "access_token"
    # Fields compared with what the AI read (all optional); the phone is compared with partner phone/mobile
    partner_field: str | None = None
    total_field: str | None = None
    person_field: str | None = None

    @field_validator("number")
    @classmethod
    def _number(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"regex xato: {exc}") from None
        return value

    @field_validator("codes")
    @classmethod
    def _codes(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                groups = set(re.compile(value).groupindex)
            except re.error as exc:
                raise ValueError(f"regex xato ({value}): {exc}") from None
            if not groups & {"id", "name"}:
                raise ValueError(f"'{value}': 'id' yoki 'name' nomli guruh kerak")
            if "token" in groups and "id" not in groups:
                raise ValueError(f"'{value}': 'token' faqat 'id' bilan birga ishlatiladi")
        return values


class DocumentsConfig(_Strict):
    documents: list[DocumentType] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        keys = [d.key for d in self.documents]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"takrorlangan key: {dupes}")
        return self


# --------------------------------------------------------------------------- .env


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    bot_token: SecretStr
    # Comma separated. A private chat's id is the user's id; group chat ids are negative (-100...).
    allowed_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_api_key: SecretStr

    openai_api_key: SecretStr | None = None

    # Optional overrides; by default SQLite lives in <data_dir>/docbot.db.
    database_url: str | None = None
    data_dir: Path | None = None

    @field_validator("allowed_chat_ids", "admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            try:
                return [int(part) for part in re.split(r"[,;\s]+", value.strip()) if part]
            except ValueError:
                raise ValueError("vergul bilan ajratilgan butun sonlar bo'lishi kerak") from None  # do not echo the input
        return value

    @field_validator("openai_api_key", "database_url", "data_dir", mode="before")
    @classmethod
    def _blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("odoo_url")
    @classmethod
    def _odoo_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if PLACEHOLDER not in value and not re.match(r"^https?://\S+$", value):
            raise ValueError("must start with http:// or https://")
        return value


# --------------------------------------------------------------------------- loading


@dataclass(frozen=True)
class AppConfig:
    home: Path
    env: EnvSettings
    settings: AppSettings
    documents: DocumentsConfig

    @property
    def data_dir(self) -> Path:
        path = self.env.data_dir or Path("data")
        return path if path.is_absolute() else self.home / path

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / "docbot.lock"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"

    @property
    def chats_path(self) -> Path:
        """Groups registered at run time (see telegram.chats)."""
        return self.data_dir / "chats.json"

    @property
    def database_url(self) -> str:
        if self.env.database_url:
            return self.env.database_url
        return "sqlite+aiosqlite:///" + (self.data_dir / "docbot.db").as_posix()

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.images_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)

    def secret_values(self) -> list[str]:
        """Exact secret strings, for the log/DB redaction registry."""
        values = [self.env.bot_token.get_secret_value(), self.env.odoo_api_key.get_secret_value()]
        if self.env.openai_api_key:
            values.append(self.env.openai_api_key.get_secret_value())
        password = urlsplit(self.database_url).password
        if password:
            values.append(password)
        return values


def resolve_home(explicit: Path | None = None) -> Path:
    """Project root: explicit argument, then $DOCBOT_HOME, then the working directory, then this checkout."""
    for label, raw in (("--home", explicit), (HOME_ENV_VAR, os.environ.get(HOME_ENV_VAR))):
        if raw:
            path = Path(raw)
            if not (path / "config").is_dir():
                raise ConfigError([f"{label}={path}: 'config' papkasi topilmadi"])
            return path.resolve()
    for path in (Path.cwd(), Path(__file__).resolve().parents[2]):
        if (path / "config").is_dir():
            return path.resolve()
    raise ConfigError(
        [f"'config' papkasi topilmadi (joriy papka: {Path.cwd()}). --home yoki {HOME_ENV_VAR} orqali loyiha papkasini ko'rsating"]
    )


def _format_errors(prefix: str, exc: ValidationError, *, upper: bool = False) -> list[str]:
    problems = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        loc = ".".join(str(part) for part in err["loc"])
        loc = loc.upper() if upper else loc
        problems.append(f"{prefix}{loc + ': ' if loc else ''}{err['msg']}")
    return problems


def _read_yaml(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.is_file():
        return {}, [f"{path.name}: fayl topilmadi ({path})"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (qator {mark.line + 1})" if mark else ""
        return {}, [f"{path.name}: YAML xato{where}"]
    if data is None:
        return {}, []
    if not isinstance(data, dict):
        return {}, [f"{path.name}: fayl mapping (kalit: qiymat) bo'lishi kerak"]
    return data, []


def load_config(home: Path | None = None, *, env_file: Path | None = None) -> AppConfig:
    """Load and validate everything; raise one ConfigError listing all problems."""
    home = resolve_home(home)
    problems: list[str] = []

    env_path = env_file if env_file is not None else home / ".env"
    env: EnvSettings | None = None
    try:
        env = EnvSettings(_env_file=env_path if env_path.is_file() else None)
    except ValidationError as exc:
        if not env_path.is_file():
            problems.append(f".env fayli topilmadi ({env_path}); .env.example dan nusxa oling")
        problems += _format_errors(".env: ", exc, upper=True)

    settings: AppSettings | None = None
    raw, errs = _read_yaml(home / "config" / "settings.yaml")
    problems += errs
    if not errs:
        try:
            settings = AppSettings.model_validate(raw)
        except ValidationError as exc:
            problems += _format_errors("settings.yaml: ", exc)

    documents: DocumentsConfig | None = None
    raw, errs = _read_yaml(home / "config" / "documents.yaml")
    problems += errs
    if not errs:
        try:
            documents = DocumentsConfig.model_validate(raw)
        except ValidationError as exc:
            problems += _format_errors("documents.yaml: ", exc)

    if problems or env is None or settings is None or documents is None:
        raise ConfigError(problems)
    return AppConfig(home=home, env=env, settings=settings, documents=documents)


# --------------------------------------------------------------------------- readiness


def _walk_placeholders(node: Any, path: str = "") -> Iterator[str]:
    if isinstance(node, str):
        if PLACEHOLDER in node:
            yield path
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _walk_placeholders(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_placeholders(value, f"{path}[{index}]")


def find_placeholders(cfg: AppConfig) -> list[str]:
    """Names (never values) of settings that still hold the template placeholder."""
    found = []
    for name in EnvSettings.model_fields:
        value = getattr(cfg.env, name)
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        if isinstance(text, str) and PLACEHOLDER in text:
            found.append(f".env: {name.upper()}")
    found += [f"documents.yaml: {path}" for path in _walk_placeholders(cfg.documents.model_dump(mode="json"))]
    return found


def readiness_issues(cfg: AppConfig) -> list[str]:
    """Reasons the bot must not run yet. Templates are valid configs, but not runnable ones."""
    issues = [f"{item} hali to'ldirilmagan (placeholder)" for item in find_placeholders(cfg)]
    if not cfg.env.allowed_chat_ids:
        issues.append(".env: ALLOWED_CHAT_IDS bo'sh (bot barcha chatlarni e'tiborsiz qoldiradi)")
    if not cfg.env.admin_ids:
        issues.append(".env: ADMIN_IDS bo'sh (xatolar haqida xabar beriladigan admin yo'q)")
    if cfg.settings.ai.enabled and cfg.env.openai_api_key is None:
        issues.append(".env: OPENAI_API_KEY bo'sh, lekin settings.yaml'da ai.enabled: true")
    return issues
