"""The only Agents SDK implementation: one agent, one model step per request."""

import asyncio
import logging
import os
from copy import deepcopy
from importlib.metadata import version

from workpilot.config import MODEL_TIMEOUT_S
from workpilot.errors import ExitCode, WorkPilotError
from workpilot.llm.client import ModelRequest, ModelResponse, ToolCall

OFFICIAL_BASE_URL = "https://api.openai.com/v1"
SDK_VERSION = "0.24.0"


def _sdk():
    if os.environ.get("OPENAI_AGENTS_DISABLE_TRACING") != "1":
        raise WorkPilotError("远端 tracing 未关闭，拒绝加载模型 SDK。")
    os.environ["OPENAI_AGENTS_DONT_LOG_MODEL_DATA"] = "1"
    os.environ["OPENAI_AGENTS_DONT_LOG_TOOL_DATA"] = "1"
    try:
        import agents

        if version("openai-agents") != SDK_VERSION:
            raise WorkPilotError("Agents SDK 版本与已验证版本不符，请按 uv.lock 安装。")
        agents.set_tracing_disabled(True)
        agents.set_trace_processors([])
        for name in ("openai", "openai.agents", "httpx2", "httpcore2"):
            logging.getLogger(name).setLevel(logging.CRITICAL)
        return agents
    except ImportError:
        raise WorkPilotError("Agents SDK 未正确安装，请按 uv.lock 安装依赖。") from None


def tracing_status() -> tuple[str, bool]:
    """A NoOpTrace probe creates no client, reads no key, and sends no network request."""
    sdk = _sdk()
    from agents.tracing.traces import NoOpTrace

    return SDK_VERSION, isinstance(sdk.trace("workpilot-offline-check"), NoOpTrace)


async def _no_sdk_execution(_context, _arguments):
    raise RuntimeError("Tool execution belongs to WorkPilot's local policy gate.")


class OpenAIModelClient:
    def __init__(self, model: str, api_key: str, *, http_client=None) -> None:
        sdk = _sdk()
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        if not model or not api_key.strip():
            raise WorkPilotError("ask 需要设置 OPENAI_API_KEY 和 WORKPILOT_MODEL。")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=OFFICIAL_BASE_URL,
            max_retries=0,
            timeout=MODEL_TIMEOUT_S,
            organization="",
            project="",
            http_client=http_client
            or DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False),
        )
        self._agent = sdk.Agent(
            name="WorkPilot", model=sdk.OpenAIResponsesModel(model, self._client)
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        sdk = _sdk()
        instructions, items = [], []
        for message in request.messages:
            if message.role == "system":
                instructions.append(message.content)
            elif message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.call_id,
                        "output": message.content,
                    }
                )
            elif message.continuation:
                items.extend(deepcopy(message.continuation))
            elif message.tool_calls:
                if message.content:
                    items.append({"role": "assistant", "content": message.content})
                items.extend(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in message.tool_calls
                )
            else:
                items.append({"role": message.role, "content": message.content})
        self._agent.instructions = "\n".join(instructions)
        self._agent.tools = [
            sdk.FunctionTool(
                name=tool.name,
                description=tool.description,
                params_json_schema=deepcopy(tool.parameters),
                on_invoke_tool=_no_sdk_execution,
            )
            for tool in request.tools
        ]
        self._agent.model_settings = sdk.ModelSettings(
            max_tokens=request.max_output_tokens,
            timeout=request.timeout_s,
            parallel_tool_calls=False,
            tool_choice="auto" if request.tools else "none",
            store=False,
            truncation="disabled",
            response_include=["reasoning.encrypted_content"],
        )
        try:
            async with asyncio.timeout(request.timeout_s):
                response = await self._agent.model.get_response(
                    system_instructions=self._agent.instructions,
                    input=items,
                    model_settings=self._agent.model_settings,
                    tools=self._agent.tools,
                    output_schema=None,
                    handoffs=[],
                    tracing=sdk.ModelTracing.DISABLED,
                    previous_response_id=None,
                    conversation_id=None,
                    prompt=None,
                )
            text, calls = [], []
            for item in response.output:
                if getattr(item, "status", None) not in {None, "completed"}:
                    raise ValueError("Incomplete model output.")
                if item.type == "message":
                    for content in item.content:
                        if content.type == "output_text":
                            text.append(content.text)
                        elif content.type != "refusal":
                            raise ValueError("Unsupported model content.")
                elif item.type == "function_call":
                    calls.append(ToolCall(item.call_id, item.name, item.arguments))
                elif item.type != "reasoning":
                    raise ValueError("Unsupported model capability.")
            return ModelResponse(
                text="\n".join(text),
                tool_calls=tuple(calls),
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                continuation=tuple(response.to_input_items()),
            )
        except TimeoutError:
            raise WorkPilotError("模型请求超时。", ExitCode.REMOTE) from None
        except Exception:
            raise WorkPilotError(
                "模型请求失败；未输出原始响应或错误详情。", ExitCode.REMOTE
            ) from None

    async def aclose(self) -> None:
        await self._client.close()
