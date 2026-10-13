"""
Tests for PDF document generation — ReportLab.
Tests rendering with mock Odoo data.
"""
import pytest
import os
import tempfile
from app.services.pdf_documents import (
    render_sale_order_pdf,
    render_invoice_pdf,
    render_payment_receipt_pdf,
    render_delivery_slip_pdf,
    _safe,
    _fmt_money,
)


class TestHelpers:
    """Test helper functions."""

    def test_safe_escapes_html(self):
        """_safe should escape HTML characters."""
        result = _safe('<script>alert("xss")</script>')
        assert "<script>" not in result
        assert "&lt;" in result

    def test_safe_handles_none(self):
        """_safe should handle None gracefully."""
        assert _safe(None) == ""

    def test_safe_handles_empty_string(self):
        assert _safe("") == ""

    def test_fmt_money_normal(self):
        """_fmt_money should format numbers with commas."""
        result = _fmt_money(1234567.89)
        assert "1,234,567.89" in result
        assert "so'm" in result

    def test_fmt_money_zero(self):
        result = _fmt_money(0)
        assert "0.00" in result

    def test_fmt_money_none(self):
        """_fmt_money should handle None."""
        result = _fmt_money(None)
        assert "0.00" in result

    def test_fmt_money_custom_currency(self):
        result = _fmt_money(100, "USD")
        assert "USD" in result


class TestSaleOrderPDF:
    """Test sale order PDF rendering."""

    def test_render_basic(self):
        """Should render a basic sale order PDF."""
        data = {
            "order": {
                "name": "SO001",
                "date_order": "2026-09-01",
                "partner_id": [1, "Test Partner"],
                "user_id": [2, "Test User"],
                "amount_total": 1500000,
            },
            "lines": [
                {
                    "product_id": [10, "Product A"],
                    "product_uom_qty": 5,
                    "price_unit": 200000,
                    "price_subtotal": 1000000,
                },
                {
                    "product_id": [11, "Product B"],
                    "product_uom_qty": 1,
                    "price_unit": 500000,
                    "price_subtotal": 500000,
                },
            ],
        }
        filepath = render_sale_order_pdf(data)
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0
        os.unlink(filepath)

    def test_render_empty_lines(self):
        """Should render with no lines."""
        data = {"order": {"name": "SO002", "amount_total": 0}, "lines": []}
        filepath = render_sale_order_pdf(data)
        assert os.path.exists(filepath)
        os.unlink(filepath)


class TestInvoicePDF:
    """Test invoice PDF rendering."""

    def test_render_basic(self):
        data = {
            "invoice": {
                "name": "INV001",
                "invoice_date": "2026-09-01",
                "partner_id": [1, "Test Partner"],
                "amount_total": 2000000,
                "amount_tax": 300000,
                "amount_residual": 2000000,
                "payment_state": "not_paid",
            },
            "lines": [
                {
                    "name": "Service fee",
                    "quantity": 1,
                    "price_unit": 2000000,
                    "price_subtotal": 2000000,
                },
            ],
        }
        filepath = render_invoice_pdf(data)
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0
        os.unlink(filepath)


class TestPaymentReceiptPDF:
    """Test payment receipt PDF rendering."""

    def test_render_basic(self):
        data = {
            "payment": {
                "name": "PAY001",
                "date": "2026-09-01",
                "partner_id": [1, "Test Partner"],
                "amount": 500000,
                "payment_type": "inbound",
                "state": "posted",
            },
        }
        filepath = render_payment_receipt_pdf(data)
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0
        os.unlink(filepath)


class TestDeliverySlipPDF:
    """Test delivery slip PDF rendering."""

    def test_render_basic(self):
        data = {
            "picking": {
                "name": "WH/OUT/001",
                "location_id": [1, "WH/Stock"],
                "location_dest_id": [2, "Customer"],
                "scheduled_date": "2026-09-01",
            },
            "moves": [
                {
                    "product_id": [10, "Product A"],
                    "product_uom_qty": 10,
                    "quantity_done": 10,
                },
            ],
        }
        filepath = render_delivery_slip_pdf(data)
        assert os.path.exists(filepath)
        assert os.path.getsize(filepath) > 0
        os.unlink(filepath)
