"""The only module that talks to the model provider.

Pipeline code receives an `LLM` and never imports the SDK, so tests pass a fake
and swapping Gemini for another provider means rewriting this file only.
"""

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

_RETRYABLE = {429, 500, 503, 504}


def _retryable(e: Exception) -> bool:
    """Busy / overloaded / dropped connection: worth another try. Bad request: not."""
    return isinstance(e, httpx.HTTPError) or getattr(e, "code", None) in _RETRYABLE


class LLMError(Exception):
    """The provider failed after retries. Callers degrade instead of crashing."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models: list[str] = field(default_factory=list)

    def add(self, model: str, meta) -> None:
        self.calls += 1
        self.models.append(model)
        if meta:
            self.input_tokens += meta.prompt_token_count or 0
            self.output_tokens += meta.candidates_token_count or 0


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema of the arguments


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class AgentReply:
    text: str
    calls: list[ToolCall]
    raw: object = None  # the provider's own message, replayed as-is on the next turn


# Agent history is provider-neutral: {"role": "user", "text"} | {"role": "model", "reply"}
# | {"role": "tool", "results": [(ToolCall, str)]}.


class LLM(Protocol):
    def json(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        *,
        fast: bool = False,
        think: str = "minimal",
        usage: Usage | None = None,
    ) -> T: ...

    def text(
        self, system: str, prompt: str, *, think: str = "minimal", usage: Usage | None = None
    ) -> str: ...

    def json_file(
        self,
        system: str,
        data: bytes,
        mime: str,
        prompt: str,
        schema: type[T],
        *,
        think: str = "low",
        usage: Usage | None = None,
    ) -> T: ...

    def agent_step(
        self,
        system: str,
        history: list[dict],
        tools: list[ToolSpec],
        *,
        final: bool = False,
        usage: Usage | None = None,
    ) -> AgentReply: ...


class GeminiLLM:
    def __init__(self, api_key: str | None = None):
        # Without a timeout one hung connection blocks the request forever (seen in an eval run).
        self.client = genai.Client(
            api_key=api_key or settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=settings.llm_timeout_ms),
        )

    def _generate(self, model: str, system: str, prompt, think: str, usage, **config):
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_level=think),
            **config,
        )
        # Two tries on the chosen model, then one on the fallback when it's busy.
        for attempt, name in enumerate([model, model, settings.fallback_model]):
            try:
                response = self.client.models.generate_content(
                    model=name, contents=prompt, config=cfg
                )
                if usage is not None:
                    usage.add(name, response.usage_metadata)
                return response
            except (errors.APIError, httpx.HTTPError) as e:
                code = getattr(e, "code", "network")
                log.warning("llm call failed model=%s attempt=%d code=%s", name, attempt, code)
                if not _retryable(e) or attempt == 2:
                    raise LLMError(f"{code}: {getattr(e, 'message', e)}") from e
                time.sleep(1.5 * (attempt + 1))
        raise LLMError("unreachable")

    def json(self, system, prompt, schema, *, fast=False, think="minimal", usage=None):
        model = settings.fast_model if fast else settings.answer_model
        response = self._generate(
            model,
            system,
            prompt,
            think,
            usage,
            response_mime_type="application/json",
            response_schema=schema,
        )
        try:
            return schema.model_validate_json(response.text)
        except Exception as e:
            raise LLMError(f"Model returned invalid JSON: {e}") from e

    def text(self, system, prompt, *, think="minimal", usage=None):
        return self._generate(settings.answer_model, system, prompt, think, usage).text or ""

    def json_file(self, system, data, mime, prompt, schema, *, think="low", usage=None):
        """Structured output from a file (a PDF page, a scan). The file goes inline."""
        parts = [types.Part.from_bytes(data=data, mime_type=mime), prompt]
        response = self._generate(
            settings.answer_model,
            system,
            parts,
            think,
            usage,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=16_000,  # a scan once made the model loop inside one cell
        )
        try:
            return schema.model_validate_json(response.text)
        except Exception as e:
            raise LLMError(f"Model returned invalid JSON: {e}") from e

    def agent_step(self, system, history, tools, *, final=False, usage=None):
        """One model turn with native function calling. `final` forbids further calls."""
        contents = []
        for turn in history:
            if turn["role"] == "user":
                contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=turn["text"])])
                )
            elif turn["role"] == "model":
                # Gemini 3 needs its own message back (thought signatures) to continue a loop.
                reply = turn["reply"]
                contents.append(
                    reply.raw
                    or types.Content(
                        role="model",
                        parts=[
                            types.Part.from_function_call(name=c.name, args=c.args)
                            for c in reply.calls
                        ],
                    )
                )
            else:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(name=c.name, response={"result": out})
                            for c, out in turn["results"]
                        ],
                    )
                )
        response = self._generate(
            settings.answer_model,
            system,
            contents,
            "low",
            usage,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description,
                            parameters_json_schema=t.parameters,
                        )
                        for t in tools
                    ]
                )
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="NONE" if final else "AUTO"
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        content = response.candidates[0].content if response.candidates else None
        parts = (content.parts if content else None) or []
        return AgentReply(
            text="".join(p.text for p in parts if p.text and not p.thought),
            calls=[
                ToolCall(p.function_call.name, dict(p.function_call.args or {}))
                for p in parts
                if p.function_call
            ],
            raw=content,
        )


class TapeLLM:
    """Records a real model's replies to a tape, or replays them with no key and no network.

    The browser system test replays a tape so CI is deterministic and free; the app's guards
    still check every replayed reply exactly as they check live ones. Replies are matched by
    what was asked (prompt / question / file bytes), consumed in order per question.
    SETTLE_TAPE=<path> with SETTLE_TAPE_MODE=record (live model, writes) or replay (no model).
    """

    _VOLATILE = re.compile(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?|\b\d{2}:\d{2}:\d{2}\b"
    )

    def __init__(self, path: str, mode: str, live: "GeminiLLM | None" = None):
        self.path, self.mode, self.live = Path(path), mode, live
        self.lock = threading.Lock()
        self.tape: dict[str, list] = (
            json.loads(self.path.read_text()) if self.path.exists() and mode == "replay" else {}
        )
        if mode == "record" and self.path.exists():
            self.tape = json.loads(self.path.read_text())

    @classmethod
    def _key(cls, *parts) -> str:
        text = "\x1f".join(str(p) for p in parts)
        return hashlib.sha1(cls._VOLATILE.sub("<t>", text).encode()).hexdigest()

    def _play(self, key: str, make, load):
        with self.lock:
            if self.mode == "replay":
                queue = self.tape.get(key) or []
                if not queue:
                    log.warning("tape miss %s", key)
                    raise LLMError("No recorded reply for this request (tape replay).")
                return load(queue.pop(0))
        stored, value = make()  # record: live call outside the lock (PDF pages run in parallel)
        with self.lock:
            self.tape.setdefault(key, []).append(stored)
            self.path.write_text(json.dumps(self.tape, indent=0))
        return value

    def json(self, system, prompt, schema, *, fast=False, think="minimal", usage=None):
        return self._play(
            self._key("json", schema.__name__, prompt),
            lambda: _both(self.live.json(system, prompt, schema, fast=fast, think=think, usage=usage)),
            schema.model_validate,
        )  # fmt: skip

    def text(self, system, prompt, *, think="minimal", usage=None):
        return self._play(
            self._key("text", prompt),
            lambda: (s := self.live.text(system, prompt, think=think, usage=usage), s),
            str,
        )

    def json_file(self, system, data, mime, prompt, schema, *, think="low", usage=None):
        return self._play(
            self._key("file", schema.__name__, hashlib.sha256(data).hexdigest(), prompt),
            lambda: _both(self.live.json_file(system, data, mime, prompt, schema, think=think, usage=usage)),
            schema.model_validate,
        )  # fmt: skip

    def agent_step(self, system, history, tools, *, final=False, usage=None):
        first = next((t["text"] for t in history if t["role"] == "user"), "")

        def make():
            r = self.live.agent_step(system, history, tools, final=final, usage=usage)
            return {"text": r.text, "calls": [[c.name, c.args] for c in r.calls]}, r

        return self._play(
            self._key("agent", first, len(history), final),
            make,
            lambda d: AgentReply(d["text"], [ToolCall(n, a) for n, a in d["calls"]]),
        )


def _both(model: BaseModel):
    return model.model_dump(mode="json"), model
