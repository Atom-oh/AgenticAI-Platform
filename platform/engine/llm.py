"""LLMClient 인터페이스 + 어댑터 (SPEC v2 §4). **직접 호출 금지** — 모든 호출은 engine.gate(익명화 게이트)를 지난다 (§12.1).

  ClaudeAdapter : Tier 0/1 — bedrock-runtime Converse/ConverseStream, 소스 리전 ap-northeast-2, global 프로파일(IAM SigV4)
  GemmaAdapter  : Tier 2 데모 대체 — bedrock-mantle OpenAI 호환 Chat Completions, us-west-2 직접 호출, Bearer 키
                  (Converse·InvokeModel 미지원 → 표준 라이브러리 urllib 로 직접 POST, SSE 스트리밍)
  VllmAdapter   : 운영 전환용 idc_vllm(EKS Hybrid Nodes + vLLM, OpenAI 호환) 자리 — 데모 미구성(NotImplementedError)

런타임 의존: boto3/botocore + 표준 라이브러리만 (Lambda Python 3.12, openai/requests 없음).
usage 는 두 경로 모두 {"inputTokens", "outputTokens", "totalTokens"} 로 정규화한다 (Bedrock Converse 키 이름 기준).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Iterable, Iterator, List, Optional, Tuple

try:  # 3.8+ 표준
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore

DEFAULT_CLAUDE_MODEL = "global.anthropic.claude-sonnet-5"
DEFAULT_GEMMA_MODEL = "google.gemma-4-31b"
DEFAULT_GEMMA_BASE_URL = "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
DEFAULT_GEMMA_SECRET = "bedrock/api-key"
GEMMA_MAX_BODY_BYTES = 3_500_000        # §4-2 요청 페이로드 상한 3.5MB
_MANTLE_REGION_RE = re.compile(r"bedrock-mantle\.([a-z0-9-]+)\.api\.aws")


class LLMError(RuntimeError):
    """어댑터 호출 실패 (메시지에 비밀값·프롬프트 원문을 넣지 않는다)."""


class LLMHttpError(LLMError):
    def __init__(self, status: int, message: str, endpoint: str = "") -> None:
        super().__init__(f"HTTP {status} from {endpoint or 'llm endpoint'}: {message[:300]}")
        self.status = status
        self.endpoint = endpoint


def normalize_usage(raw) -> dict:
    """Bedrock Converse usage → 정수화. 없는 키는 0. 추가 키(cacheRead… 등)는 유지."""
    out = dict(raw or {})
    for k in ("inputTokens", "outputTokens", "totalTokens"):
        try:
            out[k] = int(out.get(k, 0) or 0)
        except (TypeError, ValueError):
            out[k] = 0
    if not out["totalTokens"]:
        out["totalTokens"] = out["inputTokens"] + out["outputTokens"]
    return out


def normalize_openai_usage(raw) -> dict:
    """OpenAI 호환 usage(prompt_tokens/completion_tokens/total_tokens) → Converse 키 이름."""
    raw = raw or {}
    return normalize_usage({"inputTokens": raw.get("prompt_tokens", 0), "outputTokens": raw.get("completion_tokens", 0),
                            "totalTokens": raw.get("total_tokens", 0)})


class AdapterStream:
    """어댑터가 돌려주는 토큰 스트림. 반복이 끝나면 .usage / .stop_reason 이 채워진다."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.usage: dict = {}
        self.stop_reason: str = ""

    def __iter__(self) -> Iterator[str]:  # pragma: no cover — 서브클래스가 구현
        raise NotImplementedError


class ListStream(AdapterStream):
    """이미 완성된 텍스트를 1청크로 흘려보내는 스트림 (스트리밍 미지원 응답 폴백용 — 사실대로 non_stream 표기)."""

    def __init__(self, model_id: str, text: str, usage: dict, stop_reason: str = "") -> None:
        super().__init__(model_id)
        self._text = text
        self._usage = usage
        self._stop = stop_reason
        self.non_stream = True

    def __iter__(self) -> Iterator[str]:
        if self._text:
            yield self._text
        self.usage = self._usage
        self.stop_reason = self._stop


class LLMClient(Protocol):
    """두 경로가 공유하는 최소 표면. route/tier/model_id/endpoint 는 속성으로 노출한다."""
    model_id: str
    route: str
    tier: str
    endpoint: str
    region: str

    def generate(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> Tuple[str, dict]: ...

    def stream(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> AdapterStream: ...


# ---------------------------------------------------------------------------
# Claude — bedrock-runtime Converse (Tier 0/1)
# ---------------------------------------------------------------------------
class ClaudeStream(AdapterStream):
    def __init__(self, events: Iterable[dict], model_id: str) -> None:
        super().__init__(model_id)
        self._events = events

    def __iter__(self) -> Iterator[str]:
        for ev in self._events:
            delta = ev.get("contentBlockDelta", {}).get("delta", {}).get("text")
            if delta:
                yield delta
            if "messageStop" in ev:
                self.stop_reason = ev["messageStop"].get("stopReason", "") or ""
            if "metadata" in ev:
                self.usage = normalize_usage(ev["metadata"].get("usage", {}))


class ClaudeAdapter:
    route = "claude"
    tier = "0/1"
    endpoint = "bedrock-runtime"

    def __init__(self, model_id: Optional[str] = None, region: Optional[str] = None, client=None) -> None:
        self.model_id = model_id or os.environ.get("GEN_MODEL", DEFAULT_CLAUDE_MODEL)
        self.region = region or os.environ.get("AWS_REGION", "ap-northeast-2")
        self._rt = client  # 지연 생성 (테스트는 페이크 주입)

    def _client(self):
        if self._rt is None:
            import boto3
            self._rt = boto3.client("bedrock-runtime", region_name=self.region)
        return self._rt

    @staticmethod
    def _inference_config(max_tokens: int, temperature: float) -> dict:
        """Claude 5 계열(global.anthropic.claude-sonnet-5)은 Converse 에서 `temperature` 를 거부한다
        (실측 2026-09-02: ValidationException "`temperature` is deprecated for this model").
        기본은 maxTokens 만 보내고, 구형 모델에 필요하면 env GEN_TEMPERATURE 로 명시적으로 켠다. temperature 인자는 인터페이스 호환용."""
        cfg = {"maxTokens": int(max_tokens)}
        env_t = os.environ.get("GEN_TEMPERATURE", "").strip()
        if env_t:
            cfg["temperature"] = float(env_t)
        return cfg

    @classmethod
    def _kwargs(cls, system: str, user: str, max_tokens: int, temperature: float, model_id: str) -> dict:
        kw = {"modelId": model_id,
              "messages": [{"role": "user", "content": [{"text": user}]}],
              "inferenceConfig": cls._inference_config(max_tokens, temperature)}
        if system:
            kw["system"] = [{"text": system}]
        return kw

    def generate(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> Tuple[str, dict]:
        r = self._client().converse(**self._kwargs(system, user, max_tokens, temperature, self.model_id))
        text = "".join(b.get("text", "") for b in r.get("output", {}).get("message", {}).get("content", []))
        return text, normalize_usage(r.get("usage", {}))

    def stream(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> ClaudeStream:
        r = self._client().converse_stream(**self._kwargs(system, user, max_tokens, temperature, self.model_id))
        return ClaudeStream(r["stream"], self.model_id)

    def converse_with_tools(self, system: str, messages: List[dict], tool_config: Optional[dict],
                            model: Optional[str] = None, max_tokens: int = 1800, temperature: float = 0.1) -> dict:
        """도구 루프용 Converse 원형 응답 (Reader). 게이트(engine.gate.ToolClient)를 통해서만 호출된다."""
        kw = {"modelId": model or self.model_id, "messages": list(messages),
              "inferenceConfig": self._inference_config(max_tokens, temperature)}
        if system:
            kw["system"] = [{"text": system}]
        if tool_config:
            kw["toolConfig"] = tool_config
        return self._client().converse(**kw)


# ---------------------------------------------------------------------------
# Gemma — bedrock-mantle OpenAI 호환 (Tier 2 데모 대체)
# ---------------------------------------------------------------------------
def iter_sse_json(lines: Iterable) -> Iterator[dict]:
    """SSE 본문에서 'data: {...}' 줄만 JSON 으로 디코드해 yield. 'data: [DONE]' 에서 끝. bytes/str 모두 허용."""
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        if isinstance(obj, dict):
            yield obj


def _content_text(content) -> str:
    """OpenAI message.content — 문자열 또는 [{"type":"text","text":...}] 목록."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return "" if content is None else str(content)


class GemmaStream(AdapterStream):
    def __init__(self, lines: Iterable, model_id: str, close=None) -> None:
        super().__init__(model_id)
        self._lines = lines
        self._close = close

    def __iter__(self) -> Iterator[str]:
        try:
            for chunk in iter_sse_json(self._lines):
                for ch in chunk.get("choices") or []:
                    delta = _content_text((ch.get("delta") or {}).get("content"))
                    if delta:
                        yield delta
                    if ch.get("finish_reason"):
                        self.stop_reason = str(ch["finish_reason"])
                if chunk.get("usage"):
                    self.usage = normalize_openai_usage(chunk["usage"])
        finally:
            if self._close:
                try:
                    self._close()
                except Exception:  # noqa: BLE001
                    pass


_secret_cache: dict = {}


def secret_api_key(secret_id: Optional[str] = None, region: Optional[str] = None, client=None) -> Optional[str]:
    """Secrets Manager 의 장기 Bedrock API 키 — **Lambda 런타임에서만** 읽는다 (코드·문서에 값 없음, §12.9).
    JSON {"api_key": ...} 또는 평문. 없으면(ResourceNotFound/AccessDenied) None → 단기 토큰 폴백.
    값은 프로세스 안에 1시간 캐시하고 어디에도 기록하지 않는다."""
    secret_id = secret_id or os.environ.get("GEMMA_API_KEY_SECRET", DEFAULT_GEMMA_SECRET)
    region = region or os.environ.get("GEMMA_API_KEY_SECRET_REGION") or os.environ.get("AWS_REGION", "ap-northeast-2")
    key = (secret_id, region)
    ent = _secret_cache.get(key)
    if ent and ent["expires"] > time.time():
        return ent["value"]
    try:
        if client is None:
            import boto3
            client = boto3.client("secretsmanager", region_name=region)
        r = client.get_secret_value(SecretId=secret_id)
        raw = r.get("SecretString") or ""
        value: Optional[str] = None
        if raw:
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    value = obj.get("api_key") or obj.get("OPENAI_API_KEY") or obj.get("apiKey") or obj.get("key")
                elif isinstance(obj, str):
                    value = obj
            except ValueError:
                value = raw.strip()
        value = value or None
    except Exception as e:  # noqa: BLE001 — 없거나 권한이 없으면 단기 토큰으로 폴백 (오류 코드만 기록)
        resp = getattr(e, "response", None)
        code = (resp.get("Error", {}) or {}).get("Code", "") if isinstance(resp, dict) else ""
        _log("llm.gemma.secret_unavailable", code=code or type(e).__name__)
        value = None
    _secret_cache[key] = {"value": value, "expires": time.time() + 3600}
    return value


def default_api_key(mantle_region: str = "us-west-2") -> Tuple[str, str]:
    """(키, 출처). 출처: 'secretsmanager' | 'sigv4-short-term'. 장기 키 우선(§16), 없으면 IAM 단기 토큰."""
    k = secret_api_key()
    if k:
        return k, "secretsmanager"
    from engine import bedrock_token
    return bedrock_token.get_token(region=mantle_region), "sigv4-short-term"


def _log(event: str, **fields) -> None:
    try:
        from common.log import log_event
        log_event(event, "", **fields)
    except Exception:  # noqa: BLE001 — 로컬 실행에서 common 이 없을 수 있다
        print(json.dumps({"event": event, **{k: str(v)[:120] for k, v in fields.items()}}, ensure_ascii=False))


class GemmaAdapter:
    route = "gemma"
    tier = "2"
    endpoint = "bedrock-mantle"

    def __init__(self, model_id: Optional[str] = None, base_url: Optional[str] = None,
                 api_key_provider=None, opener=None, timeout: int = 120) -> None:
        self.model_id = model_id or os.environ.get("GEMMA_MODEL", DEFAULT_GEMMA_MODEL)
        self.base_url = (base_url or os.environ.get("GEMMA_BASE_URL", DEFAULT_GEMMA_BASE_URL)).rstrip("/")
        m = _MANTLE_REGION_RE.search(self.base_url)
        self.region = m.group(1) if m else "us-west-2"
        self._key_provider = api_key_provider or (lambda: default_api_key(self.region))
        self._open = opener or urllib.request.urlopen
        self.timeout = timeout
        self.auth_source = ""   # 마지막 호출의 키 출처 (값 아님)

    # -- HTTP --
    def _headers(self) -> dict:
        key, source = self._key_provider()
        self.auth_source = source
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "*/*"}

    def _request(self, path: str, body: Optional[dict] = None, method: str = "POST"):
        return self._request_url(self.base_url + path, body, method)

    def _request_url(self, url: str, body: Optional[dict] = None, method: str = "POST"):
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if len(data) > GEMMA_MAX_BODY_BYTES:
                raise LLMError(f"gemma 요청 페이로드 {len(data)}B — 상한 {GEMMA_MAX_BODY_BYTES}B 초과 (§4-2)")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            return self._open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                detail = ""
            msg = detail
            try:
                obj = json.loads(detail)
                if isinstance(obj, dict):
                    err = obj.get("error")
                    msg = str((err or {}).get("message") if isinstance(err, dict) else (err or obj.get("message") or detail))
            except ValueError:
                pass
            raise LLMHttpError(e.code, msg or str(e.reason), url) from None
        except urllib.error.URLError as e:
            raise LLMError(f"gemma 엔드포인트 연결 실패 ({url}): {str(e.reason)[:160]}") from None

    def models_urls(self) -> List[str]:
        """모델 카탈로그 후보 URL. 실측(2026-09-02): mantle 은 {base}/models(=/openai/v1/models) 가 404 이고
        오리진의 /v1/models 가 응답한다 — 기본 경로를 먼저, 오리진 /v1/models 를 폴백으로."""
        origin = self.base_url.split("/openai", 1)[0] if "/openai" in self.base_url else self.base_url.rsplit("/v1", 1)[0]
        out = [self.base_url + "/models"]
        alt = origin.rstrip("/") + "/v1/models"
        if alt not in out:
            out.append(alt)
        return out

    def _chat_body(self, system: str, user: str, max_tokens: int, temperature: float, stream: bool) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {"model": self.model_id, "messages": messages, "max_tokens": int(max_tokens),
                "temperature": float(temperature), "stream": bool(stream)}
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    @staticmethod
    def parse_completion(obj: dict) -> Tuple[str, dict, str]:
        """비스트리밍 Chat Completions 응답 → (text, usage, finish_reason)."""
        choices = obj.get("choices") or []
        text = _content_text((choices[0].get("message") or {}).get("content")) if choices else ""
        finish = str(choices[0].get("finish_reason") or "") if choices else ""
        return text, normalize_openai_usage(obj.get("usage")), finish

    # -- LLMClient --
    def generate(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> Tuple[str, dict]:
        resp = self._request("/chat/completions", self._chat_body(system, user, max_tokens, temperature, stream=False))
        raw = resp.read()
        try:
            obj = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            raise LLMError("gemma 응답이 JSON 이 아닙니다") from None
        text, usage, _ = self.parse_completion(obj)
        return text, usage

    def stream(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2) -> AdapterStream:
        resp = self._request("/chat/completions", self._chat_body(system, user, max_tokens, temperature, stream=True))
        ctype = ""
        try:
            hdrs = getattr(resp, "headers", None)
            ctype = (hdrs.get("Content-Type") or "") if hdrs is not None else ""
        except Exception:  # noqa: BLE001
            ctype = ""
        if "json" in ctype.lower() and "event-stream" not in ctype.lower():
            # 서버가 스트리밍 대신 완성 응답을 준 경우 — 1청크로 흘리고 non_stream 으로 표기 (흉내 아님)
            obj = json.loads(resp.read().decode("utf-8", errors="replace"))
            text, usage, finish = self.parse_completion(obj)
            return ListStream(self.model_id, text, usage, finish)
        return GemmaStream(resp, self.model_id, close=getattr(resp, "close", None))

    def health(self) -> dict:
        """GET /models — 설정된 모델이 카탈로그에 있는지 확인. 반환 {"ok", "found", "model", "models", "status", "authSource", "error"}."""
        out = {"ok": False, "found": False, "model": self.model_id, "models": [], "status": 0, "endpoint": self.base_url,
               "modelsPath": "", "region": self.region, "authSource": "", "error": ""}
        for url in self.models_urls():
            out["modelsPath"] = url
            try:
                resp = self._request_url(url, None, method="GET")
                out["status"] = int(getattr(resp, "status", 200) or 200)
                obj = json.loads(resp.read().decode("utf-8", errors="replace"))
                ids = [str(m.get("id", "")) for m in (obj.get("data") or []) if isinstance(m, dict)]
                out["models"] = ids[:100]
                out["found"] = self.model_id in ids
                out["ok"] = out["found"]
                out["error"] = "" if out["found"] else f"모델 {self.model_id} 이(가) /models 카탈로그에 없습니다 ({len(ids)}개 중)"
                break
            except LLMHttpError as e:
                out["status"], out["error"] = e.status, str(e)
                if e.status == 404:
                    continue   # 다음 후보 경로
                break
            except Exception as e:  # noqa: BLE001
                out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                break
        out["authSource"] = self.auth_source
        return out


# ---------------------------------------------------------------------------
# idc_vllm — 운영 전환용 자리 (데모 미구성)
# ---------------------------------------------------------------------------
class VllmAdapter:
    """EKS Hybrid Nodes(IDC GPU) 의 vLLM OpenAI 호환 서버용 자리. 데모에는 GPU 가 없어 구성하지 않는다 (§11-1)."""
    route = "idc_vllm"
    tier = "2"
    endpoint = "vllm (EKS Hybrid Nodes)"
    _MSG = "idc_vllm 어댑터는 운영 전환용(EKS Hybrid Nodes + vLLM, OpenAI 호환) — 데모 미구성"

    def __init__(self, model_id: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.model_id = model_id or os.environ.get("VLLM_MODEL", "(idc_vllm 미구성)")
        self.base_url = base_url or os.environ.get("VLLM_BASE_URL", "")
        self.region = "idc"

    def generate(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2):
        raise NotImplementedError(self._MSG)

    def stream(self, system: str, user: str, max_tokens: int = 1500, temperature: float = 0.2):
        raise NotImplementedError(self._MSG)

    def health(self) -> dict:
        return {"ok": False, "found": False, "model": self.model_id, "error": self._MSG, "status": 0}
