"""Odoo webhook payload validatsiyasini (pydantic sxemalar) sinovdan o'tkazadi."""

import pytest
from pydantic import ValidationError

from app.schemas import OdooWebhookPayload


def test_pdf_base64_is_required():
    with pytest.raises(ValidationError):
        OdooWebhookPayload()


def test_accepts_username_as_list():
    payload = OdooWebhookPayload(pdf_base64="abc", user={"username": ["a", "b"]})
    assert payload.user.username == ["a", "b"]


def test_accepts_username_as_comma_separated_string():
    payload = OdooWebhookPayload(pdf_base64="abc", user={"username": "a,b"})
    assert payload.user.username == "a,b"


def test_group_id_optional_defaults_to_none():
    payload = OdooWebhookPayload(pdf_base64="abc")
    assert payload.group_id is None


def test_group_id_accepted_when_provided():
    payload = OdooWebhookPayload(pdf_base64="abc", group_id=-1001234567890)
    assert payload.group_id == -1001234567890


def test_user_and_manager_optional():
    payload = OdooWebhookPayload(pdf_base64="abc")
    assert payload.user is None
    assert payload.manager is None
