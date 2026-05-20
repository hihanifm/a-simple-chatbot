"""Fallback when lab_adapter.py is not present (implement at office)."""

from llm_client import DEFAULT_LAB_STUB_CAPABILITIES, ChatCompleteResult, LLMClient

LAB_ADAPTER_SETUP = (
    "Lab (custom API) requires lab_adapter.py. "
    "Copy lab_adapter.py.example to lab_adapter.py and implement it at the office."
)


class LabAdapterStub(LLMClient):
    adapter_kind = "lab"
    capabilities = dict(DEFAULT_LAB_STUB_CAPABILITIES)

    def __init__(self, base_url: str):
        self.base_url = base_url

    def _not_ready(self):
        raise NotImplementedError(LAB_ADAPTER_SETUP)

    def list_models(self) -> list:
        self._not_ready()

    def chat_complete(self, **kwargs) -> ChatCompleteResult:
        self._not_ready()

    def iter_chat_stream(self, **kwargs):
        self._not_ready()
