from typing import Any, Dict


LOSS_IMPLEMENTATION_VERSION = "1.0.0"
OUTPUT_RESERVE_TOKENS = 512
PINNED_ITERATE_BATCHES_PAD_TO = 32
PROJECTION_WINDOW_TOKENS = OUTPUT_RESERVE_TOKENS + PINNED_ITERATE_BATCHES_PAD_TO


class P5MLXCompletionLossError(ValueError):
    """Raised when the frozen completion-only projection contract is violated."""


def completion_projection_bounds(
    *,
    batch_token_count: int,
    prompt_offset: int,
    full_token_count: int,
) -> Dict[str, int]:
    """Return the exact hidden-state window for pinned MLX-LM mask semantics."""
    if batch_token_count < 2:
        raise P5MLXCompletionLossError("A causal-loss batch needs at least two tokens")
    input_token_count = batch_token_count - 1
    if prompt_offset < 1:
        raise P5MLXCompletionLossError("Prompt offset must be at least one token")
    if full_token_count < prompt_offset:
        raise P5MLXCompletionLossError("Full length cannot precede the prompt offset")

    first_supervised_step = prompt_offset
    last_supervised_step = min(full_token_count, input_token_count)
    if last_supervised_step < first_supervised_step:
        raise P5MLXCompletionLossError("The batch contains no supervised target")

    window_start_index = max(0, input_token_count - PROJECTION_WINDOW_TOKENS)
    first_hidden_index = first_supervised_step - 1
    last_hidden_index = last_supervised_step - 1
    if first_hidden_index < window_start_index:
        raise P5MLXCompletionLossError(
            "Supervised completion falls outside the frozen projection window"
        )
    return {
        "input_token_count": input_token_count,
        "window_start_index": window_start_index,
        "projected_position_count": input_token_count - window_start_index,
        "first_supervised_step": first_supervised_step,
        "last_supervised_step": last_supervised_step,
        "first_hidden_index": first_hidden_index,
        "last_hidden_index": last_hidden_index,
        "supervised_position_count": (
            last_supervised_step - first_supervised_step + 1
        ),
    }


def _llama_output_projection(model: Any, hidden_states: Any) -> Any:
    args = getattr(model, "args", None)
    backbone = getattr(model, "model", None)
    if args is None or backbone is None:
        raise P5MLXCompletionLossError(
            "Completion-only projection requires the pinned Llama model interface"
        )
    if getattr(args, "tie_word_embeddings", False):
        embedding = getattr(backbone, "embed_tokens", None)
        if embedding is None or not hasattr(embedding, "as_linear"):
            raise P5MLXCompletionLossError(
                "Pinned tied Llama output projection is unavailable"
            )
        return embedding.as_linear(hidden_states)
    output_head = getattr(model, "lm_head", None)
    if output_head is None:
        raise P5MLXCompletionLossError("Pinned untied Llama output head is unavailable")
    return output_head(hidden_states)


def completion_only_projection_loss(model: Any, batch: Any, lengths: Any):
    """Compute pinned mask-prompt CE without materializing prompt-token logits."""
    import mlx.core as mx
    import mlx.nn as nn

    if batch.ndim != 2 or batch.shape[0] != 1:
        raise P5MLXCompletionLossError(
            "Frozen completion-only loss supports micro-batch size one only"
        )
    if lengths.shape != (1, 2):
        raise P5MLXCompletionLossError("Frozen MLX length tensor must have shape (1, 2)")

    inputs = batch[:, :-1]
    targets = batch[:, 1:]
    window_start = max(0, inputs.shape[1] - PROJECTION_WINDOW_TOKENS)

    hidden_states = model.model(inputs)
    projected_hidden = hidden_states[:, window_start:, :]
    projected_targets = targets[:, window_start:]
    logits = _llama_output_projection(model, projected_hidden)

    steps = mx.arange(window_start + 1, targets.shape[1] + 1)
    mask = mx.logical_and(
        steps >= lengths[:, 0:1],
        steps <= lengths[:, 1:],
    )
    ce = nn.losses.cross_entropy(logits, projected_targets) * mask
    ntoks = mask.sum()
    ce = ce.astype(mx.float32).sum() / ntoks
    return ce, ntoks
