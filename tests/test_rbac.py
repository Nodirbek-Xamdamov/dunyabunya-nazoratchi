"""
Tests for RBAC (Role-Based Access Control) module.
"""
import pytest
from app.rbac import Role, ROLE_TOOLS, ROLE_PROMPTS, ROLE_NAMES


class TestRoles:
    """Test role enum and constants."""

    def test_all_roles_exist(self):
        """All 4 roles must be defined."""
        assert Role.CEO.value == "ceo"
        assert Role.MOLIYA.value == "moliya"
        assert Role.OMBOR.value == "ombor"
        assert Role.SOTUV.value == "sotuv"

    def test_role_count(self):
        """Exactly 4 roles."""
        assert len(Role) == 4


class TestRoleTools:
    """Test role-to-tool mappings."""

    def test_ceo_has_all_tools(self):
        """CEO should have the most tools (all departments)."""
        ceo_tools = ROLE_TOOLS[Role.CEO]
        assert len(ceo_tools) >= 20, f"CEO has {len(ceo_tools)} tools, expected 20+"

    def test_sales_has_only_sales_tools(self):
        """Sales should not have finance tools."""
        sales_tools = set(ROLE_TOOLS[Role.SOTUV])
        assert "search_kassa_chiqim" not in sales_tools
        assert "create_payment_draft" not in sales_tools

    def test_warehouse_has_only_warehouse_tools(self):
        """Warehouse should not have sales or finance tools."""
        warehouse_tools = set(ROLE_TOOLS[Role.OMBOR])
        assert "find_partner" not in warehouse_tools
        assert "search_kassa_chiqim" not in warehouse_tools

    def test_finance_has_only_finance_tools(self):
        """Finance should not have sales or warehouse tools."""
        finance_tools = set(ROLE_TOOLS[Role.MOLIYA])
        assert "find_partner" not in finance_tools
        assert "check_stock_quant" not in finance_tools

    def test_ceo_includes_all_manager_tools(self):
        """CEO tools should be superset of all manager tools."""
        ceo_tools = set(ROLE_TOOLS[Role.CEO])
        for role in [Role.SOTUV, Role.OMBOR, Role.MOLIYA]:
            for tool in ROLE_TOOLS[role]:
                assert tool in ceo_tools, f"CEO missing tool '{tool}' from {role.value}"

    def test_each_role_has_tools(self):
        """No role should have empty tool list."""
        for role in Role:
            assert len(ROLE_TOOLS[role]) > 0, f"Role {role.value} has no tools"

    def test_tool_names_are_strings(self):
        """All tool names should be strings."""
        for role, tools in ROLE_TOOLS.items():
            for tool in tools:
                assert isinstance(tool, str), f"Tool in {role.value} is not a string: {tool}"


class TestRolePrompts:
    """Test role prompt definitions."""

    def test_all_roles_have_prompts(self):
        """Each role must have a system prompt."""
        for role in Role:
            assert role in ROLE_PROMPTS, f"Role {role.value} has no prompt"
            assert len(ROLE_PROMPTS[role]) > 50, f"Role {role.value} prompt too short"

    def test_ceo_prompt_mentions_all_departments(self):
        """CEO prompt should mention all departments."""
        prompt = ROLE_PROMPTS[Role.CEO].lower()
        assert "sotuv" in prompt
        assert "ombor" in prompt
        assert "moliya" in prompt
        assert "analitika" in prompt

    def test_sales_prompt_mentions_sales_only(self):
        """Sales prompt should focus on sales."""
        prompt = ROLE_PROMPTS[Role.SOTUV].lower()
        assert "sotuv" in prompt
        assert "crm" in prompt or "lid" in prompt or "buyurtma" in prompt

    def test_warehouse_prompt_mentions_warehouse_only(self):
        """Warehouse prompt should focus on warehouse."""
        prompt = ROLE_PROMPTS[Role.OMBOR].lower()
        assert "ombor" in prompt or "mahsulot" in prompt

    def test_finance_prompt_mentions_finance_only(self):
        """Finance prompt should focus on finance."""
        prompt = ROLE_PROMPTS[Role.MOLIYA].lower()
        assert "moliya" in prompt or "to'lov" in prompt or "kassa" in prompt


class TestRoleNames:
    """Test role display names."""

    def test_all_roles_have_names(self):
        """Each role must have a display name."""
        for role in Role:
            assert role in ROLE_NAMES
            assert len(ROLE_NAMES[role]) > 2
