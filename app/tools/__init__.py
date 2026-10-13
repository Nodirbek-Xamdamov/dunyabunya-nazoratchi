# app/tools/__init__.py
from app.tools.connect_operator import connect_operator_tool
from app.tools.search_products import search_products_tool

TOOLS_REGISTRY = [search_products_tool, connect_operator_tool]
