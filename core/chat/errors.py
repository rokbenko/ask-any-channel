"""Chat-orchestration domain errors. Provider-level failures (bad key, rate limit, network)
are core.credentials.CredentialError / core.providers.base.ProviderError — this module only
covers chat-flow-specific failure modes."""


class EmbeddingModelMismatchError(RuntimeError):
    pass


class ChatNotFoundError(RuntimeError):
    """The chat id doesn't exist or doesn't belong to the given channel — the tenant boundary
    (CLAUDE.md rule 5) is enforced here, not trusted from the caller."""


class QuestionTooLongError(RuntimeError):
    pass
