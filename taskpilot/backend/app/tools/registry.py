from app.tools.base import ToolRegistry
from app.tools.documents import build_document_tools
from app.tools.files import build_file_tools
from app.tools.invoices import build_invoice_tools


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for tool in build_invoice_tools() + build_file_tools() + build_document_tools():
        reg.register(tool)
    return reg
