"""Typed Pydantic models for the WebSocket protocol.

Defines discriminated unions for all client-to-server and server-to-client
messages.  Wire format: {"type": "event_name", "data": {...}, "cid": "..."}
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Client -> Server messages
# ---------------------------------------------------------------------------

class _ClientBase(BaseModel):
    cid: str | None = None


class UserMessage(_ClientBase):
    type: Literal["user_message"] = "user_message"
    data: UserMessageData

class UserMessageData(BaseModel):
    text: str


class PermissionResponse(_ClientBase):
    type: Literal["permission_response"] = "permission_response"
    data: PermissionResponseData

class PermissionResponseData(BaseModel):
    request_id: str
    allowed: bool


class CancelGeneration(_ClientBase):
    type: Literal["cancel_generation"] = "cancel_generation"
    data: dict[str, Any] = Field(default_factory=dict)


class ClearConversation(_ClientBase):
    type: Literal["clear_conversation"] = "clear_conversation"
    data: dict[str, Any] = Field(default_factory=dict)


class ResumeConversation(_ClientBase):
    type: Literal["resume_conversation"] = "resume_conversation"
    data: ResumeConversationData

class ResumeConversationData(BaseModel):
    conversation_id: str


class RevertCheckpoint(_ClientBase):
    type: Literal["revert_checkpoint"] = "revert_checkpoint"
    data: RevertCheckpointData

class RevertCheckpointData(BaseModel):
    turn: int | None = None


class ListCheckpoints(_ClientBase):
    type: Literal["list_checkpoints"] = "list_checkpoints"
    data: dict[str, Any] = Field(default_factory=dict)


class ForkConversation(_ClientBase):
    type: Literal["fork_conversation"] = "fork_conversation"
    data: ForkConversationData

class ForkConversationData(BaseModel):
    conversation_id: str


class SetSystemPrompt(_ClientBase):
    type: Literal["set_system_prompt"] = "set_system_prompt"
    data: SetSystemPromptData

class SetSystemPromptData(BaseModel):
    text: str


class SetPermissionMode(_ClientBase):
    type: Literal["set_permission_mode"] = "set_permission_mode"
    data: SetPermissionModeData

class SetPermissionModeData(BaseModel):
    mode: str


class CyclePermissionMode(_ClientBase):
    type: Literal["cycle_permission_mode"] = "cycle_permission_mode"
    data: dict[str, Any] = Field(default_factory=dict)


class GenerateTitle(_ClientBase):
    type: Literal["generate_title"] = "generate_title"
    data: dict[str, Any] = Field(default_factory=dict)


class SummarizeContext(_ClientBase):
    type: Literal["summarize_context"] = "summarize_context"
    data: dict[str, Any] = Field(default_factory=dict)


class SetModel(_ClientBase):
    type: Literal["set_model"] = "set_model"
    data: SetModelData

class SetModelData(BaseModel):
    model: str


ClientMessage = Annotated[
    UserMessage
    | PermissionResponse
    | CancelGeneration
    | ClearConversation
    | ResumeConversation
    | RevertCheckpoint
    | ListCheckpoints
    | ForkConversation
    | SetSystemPrompt
    | SetPermissionMode
    | CyclePermissionMode
    | GenerateTitle
    | SummarizeContext
    | SetModel,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Server -> Client messages
# ---------------------------------------------------------------------------

class _ServerBase(BaseModel):
    cid: str | None = None


# -- connected --

class ConnectedUserInfo(BaseModel):
    id: str
    email: str
    display_name: str

class ConnectedData(BaseModel):
    session_id: str
    model: str
    available_models: list[str]
    skills: list[dict[str, Any]]
    user: ConnectedUserInfo

class Connected(_ServerBase):
    type: Literal["connected"] = "connected"
    data: ConnectedData


# -- error --

class ErrorData(BaseModel):
    message: str

class Error(_ServerBase):
    type: Literal["error"] = "error"
    data: ErrorData


# -- text_delta --

class TextDeltaData(BaseModel):
    text: str

class TextDelta(_ServerBase):
    type: Literal["text_delta"] = "text_delta"
    data: TextDeltaData


# -- tool_start --

class ToolStartData(BaseModel):
    tool_id: str
    name: str
    input: dict[str, Any]

class ToolStart(_ServerBase):
    type: Literal["tool_start"] = "tool_start"
    data: ToolStartData


# -- tool_end --

class ToolEndData(BaseModel):
    tool_id: str
    result: str
    is_error: bool

class ToolEnd(_ServerBase):
    type: Literal["tool_end"] = "tool_end"
    data: ToolEndData


# -- image --

class ImageData(BaseModel):
    tool_id: str
    name: str
    mime_type: str
    data_base64: str

class Image(_ServerBase):
    type: Literal["image"] = "image"
    data: ImageData


# -- chart --

class ChartData(BaseModel):
    tool_id: str
    title: str
    plotly_json: str

class Chart(_ServerBase):
    type: Literal["chart"] = "chart"
    data: ChartData


# -- table --

class TableData(BaseModel):
    tool_id: str
    html: str

class Table(_ServerBase):
    type: Literal["table"] = "table"
    data: TableData


# -- html_embed --

class HtmlEmbedData(BaseModel):
    tool_id: str
    name: str
    html: str

class HtmlEmbed(_ServerBase):
    type: Literal["html_embed"] = "html_embed"
    data: HtmlEmbedData


# -- permission_request --

class PermissionRequestData(BaseModel):
    request_id: str
    tool_name: str
    summary: str
    permission_mode: str

class PermissionRequest(_ServerBase):
    type: Literal["permission_request"] = "permission_request"
    data: PermissionRequestData


# -- notification --

class NotificationData(BaseModel):
    task_id: str
    message: str

class Notification(_ServerBase):
    type: Literal["notification"] = "notification"
    data: NotificationData


# -- skills_updated --

class SkillsUpdatedData(BaseModel):
    skills: list[dict[str, Any]]

class SkillsUpdated(_ServerBase):
    type: Literal["skills_updated"] = "skills_updated"
    data: SkillsUpdatedData


# -- skill_activated --

class SkillActivatedData(BaseModel):
    name: str
    description: str
    type: str

class SkillActivated(_ServerBase):
    type: Literal["skill_activated"] = "skill_activated"
    data: SkillActivatedData


# -- response_end --

class ResponseEnd(_ServerBase):
    type: Literal["response_end"] = "response_end"
    data: dict[str, Any] = Field(default_factory=dict)


# -- generation_cancelled --

class GenerationCancelled(_ServerBase):
    type: Literal["generation_cancelled"] = "generation_cancelled"
    data: dict[str, Any] = Field(default_factory=dict)


# -- turn_complete --

class TurnCompleteData(BaseModel):
    conversation_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    context_tokens: int = 0

class TurnComplete(_ServerBase):
    type: Literal["turn_complete"] = "turn_complete"
    data: TurnCompleteData


# -- conversation_loaded --

class ConversationLoadedData(BaseModel):
    conversation_id: str
    message_count: int
    model: str
    messages: list[dict[str, Any]]
    context_tokens: int = 0

class ConversationLoaded(_ServerBase):
    type: Literal["conversation_loaded"] = "conversation_loaded"
    data: ConversationLoadedData


# -- conversation_forked --

class ConversationForkedData(BaseModel):
    source_conversation_id: str
    conversation_id: str
    message_count: int

class ConversationForked(_ServerBase):
    type: Literal["conversation_forked"] = "conversation_forked"
    data: ConversationForkedData


# -- title_updated --

class TitleUpdatedData(BaseModel):
    conversation_id: str
    title: str

class TitleUpdated(_ServerBase):
    type: Literal["title_updated"] = "title_updated"
    data: TitleUpdatedData


# -- context_summarized --

class ContextSummarizedData(BaseModel):
    original_count: int
    new_count: int
    summary: str

class ContextSummarized(_ServerBase):
    type: Literal["context_summarized"] = "context_summarized"
    data: ContextSummarizedData


# -- model_changed --

class ModelChangedData(BaseModel):
    model: str

class ModelChanged(_ServerBase):
    type: Literal["model_changed"] = "model_changed"
    data: ModelChangedData


# -- permission_mode_changed --

class PermissionModeChangedData(BaseModel):
    mode: str

class PermissionModeChanged(_ServerBase):
    type: Literal["permission_mode_changed"] = "permission_mode_changed"
    data: PermissionModeChangedData


# -- checkpoint_reverted --

class CheckpointRevertedData(BaseModel):
    reverted_files: list[str]
    remaining: int

class CheckpointReverted(_ServerBase):
    type: Literal["checkpoint_reverted"] = "checkpoint_reverted"
    data: CheckpointRevertedData


# -- checkpoints --

class CheckpointEntry(BaseModel):
    file_path: str
    turn: int
    size: int
    existed: bool

class CheckpointsData(BaseModel):
    entries: list[CheckpointEntry]

class Checkpoints(_ServerBase):
    type: Literal["checkpoints"] = "checkpoints"
    data: CheckpointsData


ServerMessage = Annotated[
    Connected
    | Error
    | TextDelta
    | ToolStart
    | ToolEnd
    | Image
    | Chart
    | Table
    | HtmlEmbed
    | PermissionRequest
    | Notification
    | SkillsUpdated
    | SkillActivated
    | ResponseEnd
    | GenerationCancelled
    | TurnComplete
    | ConversationLoaded
    | ConversationForked
    | TitleUpdated
    | ContextSummarized
    | ModelChanged
    | PermissionModeChanged
    | CheckpointReverted
    | Checkpoints,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

from pydantic import TypeAdapter

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def validate_incoming(raw: dict[str, Any]) -> ClientMessage:
    """Validate a raw JSON dict and return the typed client message."""
    return _client_adapter.validate_python(raw)
