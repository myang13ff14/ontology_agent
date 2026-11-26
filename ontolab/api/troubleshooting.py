"""API for the troubleshooting workspace (the diagram-based ontology)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..agents.memory import ConversationMemory
from ..agents.runtime import AgentRuntime
from ..errors import ToolError
from ..llm.base import LLMError
from ..llm.factory import make_llm
from ..troubleshooting.workspace import CONFIG, Workspace

router = APIRouter(prefix="/troubleshooting", tags=["troubleshooting"])
state: dict[str, Any] = {}


def install(workspace: Workspace, llm=None) -> None:
    state["workspace"] = workspace
    state["runtime"] = AgentRuntime(workspace, llm)
    state["memory"] = ConversationMemory(max_turns=40, ttl_s=workspace.settings.memory_ttl_s)
    state["cases"] = {}  # conversation id -> case id


def ws() -> Workspace:
    return state["workspace"]


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = Field(default=None, description="Omit to open a new case.")
    device_id: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    max_tool_calls: int | None = Field(default=None, ge=0, le=40)


@router.get("/status")
def status() -> dict:
    return ws().status()


@router.get("/devices")
def devices() -> dict:
    return {"devices": [{"device_id": d["node_id"], "name": d["canonical_name"]} for d in ws().store.all_nodes("Device")]}


@router.get("/tools")
def tools() -> dict:
    return {"tools": [ws().registry.describe(name) for name in CONFIG.tools], "system_prompt": ws().system_prompt("<case id>")}


@router.get("/cases")
def cases() -> dict:
    return {"cases": ws().engine.list_cases()}


@router.get("/cases/{case_id}")
def case(case_id: str) -> dict:
    try:
        return ws().engine.case_state(case_id)
    except ToolError as exc:
        raise HTTPException(404, exc.message) from None


@router.post("/chat")
def chat(body: ChatRequest) -> dict:
    """One customer message. The first message of a conversation opens a case with that message as its description."""
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Message is empty.")
    memory: ConversationMemory = state["memory"]
    if body.conversation_id:
        conversation = memory.get(body.conversation_id)
        if conversation is None or body.conversation_id not in state["cases"]:
            raise HTTPException(404, "This conversation has expired or does not exist. Start a new case.")
        case_id = state["cases"][body.conversation_id]
    else:
        try:
            case_id = ws().engine.open_case(message, body.device_id)["case_id"]
        except ToolError as exc:
            raise HTTPException(400, exc.message) from None
        conversation = memory.start(CONFIG.key)
        state["cases"][conversation.conversation_id] = case_id
    if not conversation.lock.acquire(blocking=False):
        raise HTTPException(409, "Still answering the previous message.")
    try:
        llm = make_llm(body.llm_provider, body.llm_model) if (body.llm_provider or body.llm_model) else None
        trace = state["runtime"].run(message, CONFIG.key, llm=llm, config=CONFIG, system=ws().system_prompt(case_id),
                                     history=memory.history(conversation), max_tool_calls=body.max_tool_calls or 16)
        turn_messages = trace.pop("_turn_messages")
        if trace["stop_reason"] != "llm_error":
            memory.remember(conversation, message, turn_messages)
        trace["conversation"] = memory.describe(conversation)
        return {"conversation_id": conversation.conversation_id, "case_id": case_id, "trace": trace,
                "case": ws().engine.case_state(case_id)}
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        conversation.lock.release()


@router.post("/reset")
def reset() -> dict:
    """Restore the ontology file: the three example cases return and lab-created cases are removed."""
    state["cases"].clear()
    return {"loaded": ws().reset(), "status": ws().status()}
