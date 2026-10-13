"""app.utils.cleanup_pdf_if_no_longer_needed uchun testlar: bitta pdf_path bir nechta
Order (individual + guruh) tomonidan bo'lishilganda, fayl faqat HAMMASI terminal
holatga (tasdiqlangan/rad etilgan/o'zgartirish so'ralgan/muddati o'tgan) yetgach
o'chirilishini tekshiradi. Guruh buyurtmalari ham xuddi individual kabi
confirmation_status orqali baholanadi (poll mexanizmi olib tashlangan)."""

import os

from app.database import async_session_maker
from app.models import ConfirmationStatus, Order, OrderStatus
from app.utils import cleanup_pdf_if_no_longer_needed


def _make_pdf(tmp_path, name="test.pdf") -> str:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4 fake")
    return str(path)


async def _make_order(session, pdf_path, **overrides) -> Order:
    defaults = dict(
        raw_username="user",
        normalized_username="user",
        order_details="{}",
        pdf_path=pdf_path,
        status=OrderStatus.PENDING.value,
        confirmation_status=ConfirmationStatus.PENDING.value,
    )
    defaults.update(overrides)
    order = Order(**defaults)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def test_single_confirmed_order_deletes_file(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        order = await _make_order(session, pdf_path, confirmation_status=ConfirmationStatus.CONFIRMED.value)
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    assert not os.path.exists(pdf_path)


async def test_still_pending_order_keeps_file(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        order = await _make_order(session, pdf_path)
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    assert os.path.exists(pdf_path)


async def test_shared_pdf_path_keeps_file_until_all_orders_are_terminal(tmp_path):
    """Bitta webhook chaqiruvida ikkita username bir xil faylga ega bo'lishi mumkin --
    biri qaror qilgach ham, ikkinchisi hali pending ekan fayl saqlanishi shart."""
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        await _make_order(session, pdf_path, confirmation_status=ConfirmationStatus.CONFIRMED.value)
        pending_order = await _make_order(session, pdf_path)

        await cleanup_pdf_if_no_longer_needed(session, pdf_path)
        assert os.path.exists(pdf_path)

        pending_order.confirmation_status = ConfirmationStatus.REJECTED.value
        await session.commit()
        await cleanup_pdf_if_no_longer_needed(session, pdf_path)

    assert not os.path.exists(pdf_path)


async def test_expired_order_deletes_file_even_if_confirmation_pending(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        order = await _make_order(session, pdf_path, status=OrderStatus.EXPIRED.value)
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    assert not os.path.exists(pdf_path)


async def test_group_order_keeps_file_while_pending(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        order = await _make_order(session, pdf_path, group_id=-100123, chat_id=555)
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    assert os.path.exists(pdf_path)


async def test_group_order_deletes_file_once_confirmed(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        order = await _make_order(
            session, pdf_path, group_id=-100123, chat_id=555,
            confirmation_status=ConfirmationStatus.CONFIRMED.value,
        )
        await cleanup_pdf_if_no_longer_needed(session, order.pdf_path)

    assert not os.path.exists(pdf_path)


async def test_no_matching_orders_deletes_orphaned_file(tmp_path):
    pdf_path = _make_pdf(tmp_path)

    async with async_session_maker() as session:
        await cleanup_pdf_if_no_longer_needed(session, pdf_path)

    assert not os.path.exists(pdf_path)


async def test_none_pdf_path_is_noop():
    async with async_session_maker() as session:
        await cleanup_pdf_if_no_longer_needed(session, None)
