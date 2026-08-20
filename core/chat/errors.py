"""Chat-orchestration domain errors. Provider-level failures (bad key, rate limit, network)
are core.credentials.CredentialError / core.providers.base.ProviderError — this module only
covers chat-flow-specific failure modes."""


class EmbeddingModelMismatchError(RuntimeError):
    pass


class ChatNotFoundError(RuntimeError):
    """The chat id doesn't exist — a chat_id from the caller (URL param, session state, future
    API) is never trusted without this check first."""


class QuestionTooLongError(RuntimeError):
    pass


class EmptyScopeError(RuntimeError):
    """A chat/ask scope needs at least one source channel."""


class InvalidVoiceError(RuntimeError):
    """The requested voice channel isn't one of the selected sources, or its persona is
    disabled — voice must be Neutral (None) or a member of the source set."""
