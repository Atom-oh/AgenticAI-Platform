import pytest

from engine import gate


class Adapter:
    tier = "0/1"
    model_id = "global.anthropic.claude-fable-5-1"

    def __init__(self):
        self.calls = []

    def converse_with_tools(self, system, messages, tools, **kwargs):
        self.calls.append((messages, kwargs))
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {"inputTokens": 1, "outputTokens": 1}}


def image(text="화면"):
    return {"format": "png", "bytes": b"\x89PNG\r\n\x1a\nsynthetic", "ocrStatus": "complete", "ocrText": text}


def test_vision_remains_bedrock_only_and_honors_selected_model(monkeypatch):
    adapter = Adapter()
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    text, _, info = gate.generate_with_images("규칙", "화면", [image()], model_id=adapter.model_id)
    assert text == "ok" and info["route"] == "bedrock"
    assert adapter.calls[0][1]["model"] == adapter.model_id
    assert adapter.calls[0][0][0]["content"][-1]["image"]["source"]["bytes"] == image()["bytes"]


def test_unchecked_images_and_ocr_detected_identifiers_never_reach_model(monkeypatch):
    adapter = Adapter()
    monkeypatch.setattr(gate, "adapter", lambda *args, **kwargs: adapter)
    with pytest.raises(gate.GateUnsupported):
        gate.generate_with_images("s", "u", [{**image(), "ocrStatus": "failed"}], model_id=adapter.model_id)
    with pytest.raises(gate.GateRefused):
        gate.generate_with_images("s", "u", [image("연락처 010-1234-5678")], model_id=adapter.model_id)
    with pytest.raises(ValueError):
        gate.generate_with_images("s", "u", [image()], model_id="external-provider")
    assert not adapter.calls
