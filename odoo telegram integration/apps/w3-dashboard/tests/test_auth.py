"""W3-05: logins, roles and the camera right on genesis_test. Who may see what:
viewer -> numbers and AI summaries only; manager -> + raw messages; admin (owner) -> + system page;
camera page and frames -> only with the camera right (frames also need [camera] show_frames).
User rows are created only inside a transaction that is rolled back; nothing stays in the database.
Passwords in the tests are random and never printed."""

from __future__ import annotations

import os
import secrets

import pytest
from streamlit.testing.v1 import AppTest

from core import access_log, db, users
from core.settings import load_settings
from scripts import make_fixture_frames as fixture_frames
from scripts import manage_users
from tests.conftest import DASHBOARD_DIR

ROLE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
import streamlit as st
user = {user!r}
if user is not None:
    st.session_state["auth_user"] = user
from core.settings import load_settings
from ui import components as c, shell
c.inject_css()
shell.sidebar(load_settings())
from views import {name}
{name}.render()
"""

PROBE_SCRIPT = """
import sys
sys.path.insert(0, {root!r})
import streamlit as st
user = {user!r}
if user is not None:
    st.session_state["auth_user"] = user
from ui import auth, data
ALL = ("2000-01-01 00:00:00.000000", "2100-01-01 00:00:00.000000")
frame = data.get("messages_list", *ALL, 200, include_staff=True)
st.session_state["probe_rows"] = len(frame)
st.session_state["probe_texts"] = int(frame["text"].notna().sum() + frame["transcript"].notna().sum())
checks = data.get("checks", *ALL)
st.session_state["probe_requests"] = int(checks["request_text"].notna().sum())
try:
    cam = data.get("staff_days", "2000-01-01", "2100-01-01")
    st.session_state["probe_cam"] = len(cam)
    st.session_state["probe_frames"] = int(cam["first_in_frame"].notna().sum())
except auth.AccessDenied:
    st.session_state["probe_cam"] = "denied"
"""

VIEWER = {"login": "t-viewer", "role": "viewer", "camera": False}
MANAGER = {"login": "t-manager", "role": "manager", "camera": False}
MANAGER_CAM = {"login": "t-manager-cam", "role": "manager", "camera": True}
OWNER_NO_CAM = {"login": "t-owner", "role": "admin", "camera": False}
OWNER = {"login": "t-owner-cam", "role": "admin", "camera": True}


def _password() -> str:
    return secrets.token_urlsafe(18)


@pytest.fixture(autouse=True)
def clean_env():
    yield
    for key in ("W3_FRAMES_DIR", "W3_SHOW_FRAMES"):
        os.environ.pop(key, None)
    load_settings.cache_clear()


@pytest.fixture
def frames_dir(tmp_path):
    from core import camera as cam
    from core import queries

    with db.session(load_settings()) as session:
        frame = queries.staff_days(session, "2000-01-01", "2100-01-01")
    items = [(f.path, item.staff_no, f.kind, cam.fmt_time(f.at)) for item in cam.days_from_frame(frame, load_settings().tz)
             for f in item.frames]
    fixture_frames.write_frames(tmp_path, items)
    os.environ["W3_FRAMES_DIR"] = str(tmp_path)
    load_settings.cache_clear()
    return tmp_path


def _run(script: str, user, name: str = "", query: dict | None = None) -> AppTest:
    app = AppTest.from_string(script.format(root=str(DASHBOARD_DIR), user=user, name=name), default_timeout=120)
    for key, value in (query or {"p": "last7"}).items():
        app.query_params[key] = value
    app.run()
    assert not app.exception, [item.value for item in app.exception]
    assert not app.error, [item.value for item in app.error]
    return app


def _infos(app: AppTest) -> str:
    return "\n".join(item.value for item in app.info)


def _log() -> list[dict]:
    return access_log.read(load_settings().access_log, limit=2000)


# --------------------------------------------------------------------------- passwords and rights


def test_password_hash_roundtrip_and_rules():
    password = _password()
    stored = users.hash_password(password)
    assert stored.startswith("scrypt$") and password not in stored
    assert users.check_password(password, stored)
    assert not users.check_password(password + "x", stored)
    assert stored != users.hash_password(password)  # salted
    for broken in (None, "", "plain", "bcrypt$1$2$3$4$5", "scrypt$x$8$1$AAAA$BBBB"):
        assert not users.check_password(password, broken)
    with pytest.raises(users.UserError):
        users.hash_password("short")


def test_rights_by_role():
    viewer, manager, owner = (users.Access.from_state(item) for item in (VIEWER, MANAGER, OWNER_NO_CAM))
    assert (viewer.raw, viewer.camera, viewer.system) == (False, False, False)
    assert (manager.raw, manager.camera, manager.system) == (True, False, False)
    assert (owner.raw, owner.camera, owner.system) == (True, False, True)  # camera is a separate right
    cam = users.Access.from_state(MANAGER_CAM)
    assert cam.frames(True) and not cam.frames(False) and not owner.frames(True)
    assert users.Access.from_state({"login": "x", "role": "root"}) is None
    assert users.Access.from_state(None) is None


# --------------------------------------------------------------------------- database (rolled back)


def test_users_are_written_only_by_the_tool_and_read_by_the_dashboard():
    settings = load_settings()
    with db.session(settings) as session:  # the dashboard's read-only session
        assert users.count_active(session) >= 0
        with pytest.raises(db.WriteRefused):
            session.rows("w", "INSERT INTO core.dashboard_users (login, password_hash) VALUES ('x', 'y')")
    login = f"t-{secrets.token_hex(4)}"  # a new name every run; the whole test is rolled back
    password = _password()
    with manage_users.connect(settings) as conn, conn.transaction(force_rollback=True):
        manage_users.add(conn, login.upper(), "admin", False, password)
        session = db.Session(conn, settings.query_timeout, settings.result_limit)
        assert users.authenticate(session, login, password) == users.Access(login, "admin", False)
        assert users.authenticate(session, login, password + "!") is None
        assert users.authenticate(session, "nobody-here", password) is None
        stored = session.scalar("h", "SELECT password_hash FROM core.dashboard_users WHERE login = %s", [login])
        assert stored.startswith("scrypt$") and password not in stored
        manage_users.set_role(conn, login, "viewer")
        manage_users.set_camera(conn, login, True)
        assert users.authenticate(session, login, password) == users.Access(login, "viewer", True)
        new_password = _password()
        manage_users.set_password(conn, login, new_password)
        assert users.authenticate(session, login, password) is None
        assert users.authenticate(session, login, new_password) is not None
        manage_users.set_active(conn, login, False)
        assert users.authenticate(session, login, new_password) is None  # switched off
        with pytest.raises(users.UserError):
            manage_users.add(conn, login, "viewer", False, _password())  # savepoint: the test goes on
        with pytest.raises(users.UserError):
            manage_users.set_role(conn, "nobody-here", "viewer")
        with pytest.raises(users.UserError):
            manage_users.add(conn, "bad login!", "viewer", False, _password())
    with db.session(settings) as session:
        assert session.one("gone", "SELECT 1 FROM core.dashboard_users WHERE login = %s", [login]) is None


def test_role_cannot_delete_users():
    with db.session(load_settings()) as session:
        assert session.scalar("d", "SELECT has_table_privilege(current_user, 'core.dashboard_users', 'DELETE')") is False


# --------------------------------------------------------------------------- data layer: rights after the cache


@pytest.mark.parametrize("user, raw, camera", [
    (None, False, "denied"), (VIEWER, False, "denied"), (MANAGER, True, "denied"),
    (OWNER_NO_CAM, True, "denied"), (MANAGER_CAM, True, 8), (OWNER, True, 8),
])
def test_data_layer_applies_rights(user, raw, camera, frames_dir):
    app = _run(PROBE_SCRIPT, user)
    state = app.session_state
    assert state["probe_rows"] > 0
    assert (state["probe_texts"] > 0) is raw and (state["probe_requests"] > 0) is raw
    assert state["probe_cam"] == camera
    if camera != "denied":
        assert state["probe_frames"] == 8


def test_frames_need_show_frames_as_well(frames_dir):
    os.environ["W3_SHOW_FRAMES"] = "false"
    load_settings.cache_clear()
    app = _run(PROBE_SCRIPT, OWNER)
    assert app.session_state["probe_cam"] == 8 and app.session_state["probe_frames"] == 0


# --------------------------------------------------------------------------- pages by role


def test_viewer_sees_numbers_but_no_camera_and_no_raw_messages(demo_db):
    camera = _run(ROLE_SCRIPT, VIEWER, "camera")
    assert "Kamera ma'lumotlari faqat" in _infos(camera)
    assert not camera.get("image") and not camera.get("plotly_chart") and not camera.get("metric")
    search = _run(ROLE_SCRIPT, VIEWER, "search")
    assert "Xabarlar matni faqat" in _infos(search) and not search.get("dataframe")
    system = _run(ROLE_SCRIPT, VIEWER, "system")
    assert "faqat egasiga" in _infos(system)
    group = _run(ROLE_SCRIPT, VIEWER, "groups", {"g": str(demo_db), "p": "last30"})
    assert "Xabarlar matni faqat" in _infos(group)
    assert not any('<div class="db-feed">' in item.proto.body for item in group.get("html"))
    overview = _run(ROLE_SCRIPT, VIEWER, "overview")
    assert overview.get("metric")  # numbers stay
    assert not any("Asl matn" in item.proto.body or "Asl xabar" in item.proto.body for item in overview.get("html"))
    log = _log()
    assert {"actor": "t-viewer", "action": "denied", "object_type": "page", "object_id": "camera"}.items() <= next(
        item for item in reversed(log) if item["actor"] == "t-viewer" and item["object_id"] == "camera").items()


def test_manager_sees_messages_but_camera_only_with_the_right(demo_db, frames_dir):
    search = _run(ROLE_SCRIPT, MANAGER, "search")
    assert not search.info and search.get("dataframe")
    group = _run(ROLE_SCRIPT, MANAGER, "groups", {"g": str(demo_db), "p": "last30"})
    assert any('<div class="db-feed">' in item.proto.body for item in group.get("html"))
    assert "Kamera ma'lumotlari faqat" in _infos(_run(ROLE_SCRIPT, MANAGER, "camera"))
    assert "faqat egasiga" in _infos(_run(ROLE_SCRIPT, MANAGER, "system"))
    camera = _run(ROLE_SCRIPT, MANAGER_CAM, "camera")
    assert camera.get("image") and camera.get("plotly_chart")


def test_owner_needs_the_camera_right_too(frames_dir):
    assert "Kamera ma'lumotlari faqat" in _infos(_run(ROLE_SCRIPT, OWNER_NO_CAM, "camera"))
    assert not _run(ROLE_SCRIPT, OWNER_NO_CAM, "system").info
    camera = _run(ROLE_SCRIPT, OWNER, "camera")
    assert camera.get("image")
    log = [item for item in _log() if item["actor"] == OWNER["login"]]
    assert any(item["action"] == "view" and item["object_type"] == "camera_frame" for item in log)
    assert all("password" not in str(item).lower() for item in log)


def test_nobody_signed_in_gets_nothing_sensitive(frames_dir):
    camera = _run(ROLE_SCRIPT, None, "camera")
    assert "Kamera ma'lumotlari faqat" in _infos(camera) and not camera.get("image")
    assert "Xabarlar matni faqat" in _infos(_run(ROLE_SCRIPT, None, "search"))


# --------------------------------------------------------------------------- the login form (app.py)


def _login_app(monkeypatch, *, no_users=False, access=None, known=False) -> AppTest:
    from ui import auth

    monkeypatch.setattr(auth, "_no_users", lambda settings: no_users)
    monkeypatch.setattr(auth, "_authenticate", lambda settings, login, password: (access, known or access is not None))
    app = AppTest.from_file(str(DASHBOARD_DIR / "app.py"), default_timeout=120)
    app.run()
    assert not app.exception, [item.value for item in app.exception]
    return app


def test_login_is_required(monkeypatch):
    app = _login_app(monkeypatch)
    assert [item.label for item in app.text_input] == ["Login", "Parol"]
    assert not app.title and not app.metric  # no page content before signing in


def test_no_users_shows_how_to_create_the_first_one(monkeypatch):
    app = _login_app(monkeypatch, no_users=True)
    assert app.warning and "manage_users.py add" in app.code[0].value and not app.text_input


def test_wrong_password_is_refused_and_logged_without_the_login(monkeypatch):
    app = _login_app(monkeypatch)
    secret = _password()
    app.text_input[0].input("no-such-user")
    app.text_input[1].input(secret)
    app.button[0].click().run()
    assert "Login yoki parol noto'g'ri" in app.error[0].value and "auth_user" not in app.session_state
    last = _log()[-1]
    assert last["action"] == "login_failed" and last["actor"] == "?"
    assert secret not in str(_log())


def test_viewer_login_opens_the_dashboard_without_restricted_pages(monkeypatch):
    access = users.Access("t-viewer", "viewer", False)
    app = _login_app(monkeypatch, access=access)
    app.text_input[0].input("t-viewer")
    app.text_input[1].input(_password())
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["auth_user"] == access.to_state()
    assert app.title  # the first page is shown
    assert _log()[-1]["action"] == "login" or any(item["action"] == "login" and item["actor"] == "t-viewer"
                                                  for item in _log())
    from ui import auth
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "current", lambda: access)
        assert [key for key in ("camera", "search", "system", "overview") if auth.page_allowed(key)] == ["overview"]
