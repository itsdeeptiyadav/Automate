"""Fake LLMs used to simulate misbehaving models. They subclass the deterministic
demo policy so they stay observation-driven, then deviate in one specific way."""
from app.agent.demo import DemoClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent.llm import ChainClient, ProviderInfo
from app.schemas.agent import Decision, Plan


class WrongAmountLLM(DemoClient):
    """Pretends the model mis-transcribed the amount when writing."""

    def decide(self, ctx):
        d = super().decide(ctx)
        if d.action == "tool" and d.tool == "create_invoice_record":
            d.args = {**d.args, "amount": "1.00"}
        return d


class WrongInvoiceLLM(DemoClient):
    """Pretends the model picked the OLDEST invoice instead of the latest."""

    def decide(self, ctx):
        d = super().decide(ctx)
        if d.action == "tool" and d.tool == "create_invoice_record":
            d.args = {
                "company": "Acme Corporation", "invoice_number": "ACM-2026-0388", "invoice_date": "2026-07-14",
                "due_date": "2026-08-13", "amount": "1960.00", "currency": "USD", "source_file": "invoices/invoice_004.pdf",
            }
        return d


class UnknownToolLLM(DemoClient):
    """First asks for a tool that does not exist (e.g. a shell), then behaves."""

    def __init__(self):
        self.asked = False

    def decide(self, ctx):
        if not self.asked:
            self.asked = True
            return Decision(thought="try shell", action="tool", tool="run_shell", args={"cmd": "rm -rf /"})
        return super().decide(ctx)


class TraversalLLM(DemoClient):
    def __init__(self):
        self.asked = False

    def decide(self, ctx):
        if not self.asked:
            self.asked = True
            return Decision(thought="peek", action="tool", tool="read_text_file", args={"path": "../../backend/.env"})
        return super().decide(ctx)


class EarlyFinishLLM(DemoClient):
    """Claims success without doing anything."""

    def decide(self, ctx):
        return Decision(thought="done", action="finish", message="Saved the invoice successfully!")


class WrongFilesLLM(DemoClient):
    def decide(self, ctx):
        d = super().decide(ctx)
        if d.action == "finish":
            d.data = {"files": ["workspace/misc/shopping.txt"]}
        return d


class HallucinatedPlanLLM(DemoClient):
    def plan(self, task_text, tools):
        p = super().plan(task_text, tools)
        p.params["company"] = "Zeta Dynamics"
        return p


def GarbageLLM() -> ChainClient:
    """A real LangChain chain whose model never returns valid JSON."""
    chat = FakeListChatModel(responses=["I am not JSON at all"])
    return ChainClient(chat, ProviderInfo(provider="garbage", label="Garbage", model="garbage"), max_repairs=1)
