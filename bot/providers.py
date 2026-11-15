"""Who does the analysis: PROVIDER in .env.

    claude-code  (default) Claude Code CLI on your Claude subscription. It reads the files itself and writes the card.
    anthropic    Claude API, paid per use. ANTHROPIC_API_KEY; `pip install anthropic`. Default model claude-opus-5-5.
    openai       OpenAI API. OPENAI_API_KEY and MODEL; `pip install openai`.
    openrouter   OpenRouter: any model it serves (Claude, GPT, Gemini, ...). OPENROUTER_API_KEY and MODEL;
                 `pip install openai` (OpenRouter speaks the OpenAI API).

The API providers make a single request: the instructions and the domain's files go in the system prompt (the same
for every listing, so providers can cache it), the listing's notes, links and photos go in the user message, and the
structured result comes back as JSON, including the card's text (card_markdown), which process.py writes to disk.
The SDKs are imported only when their provider is chosen, so the bot itself stays standard-library only.
"""
import base64
import json
import time

from config import Settings

PROVIDERS = ("claude-code", "anthropic", "openai", "openrouter")
OPENROUTER_URL = "https://openrouter.ai/api/v1"
# Models that take Anthropic's server-side refusal fallback ("fallbacks": "default"): on a safety decline the API
# re-runs the request on the model Anthropic recommends, inside the same call.
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class ProviderError(Exception):
    pass


def name(settings: Settings) -> str:
    """The configured provider, checked against the known ones."""
    p = settings.provider
    if p not in PROVIDERS:
        raise ProviderError("PROVIDER=%s is not one of: %s" % (p, ", ".join(PROVIDERS)))
    return p


def model_for(provider: str, settings: Settings) -> str:
    """MODEL from .env; CLAUDE_MODEL still works for claude-code. openai/openrouter have no default on purpose:
    model names change often, so pick one from the provider's model list."""
    m = settings.model
    if provider == "claude-code":
        return m or settings.claude_model or "opus"
    if provider == "anthropic":
        return m or "claude-opus-5-5"
    if not m:
        raise ProviderError("set MODEL in .env to a %s model id (see the provider's model list)" % provider)
    return m


def image_blocks(paths):
    """[(media_type, base64)] for the listing's photos."""
    out = []
    for p in paths:
        ext = p.suffix.lower().lstrip(".")
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                 "gif": "image/gif"}.get(ext, "image/jpeg")
        out.append((media, base64.standard_b64encode(p.read_bytes()).decode("ascii")))
    return out


def price(tokens_in: int, tokens_out: int, price_per_mtok: str) -> float | None:
    """Optional PRICE_PER_MTOK="input,output" (e.g. "4,20") turns API token counts into a cost estimate."""
    try:
        pin, pout = (float(x) for x in price_per_mtok.split(","))
    except ValueError:
        return None
    return round((tokens_in * pin + tokens_out * pout) / 1e6, 4)


def call_anthropic(model, system, user_text, images, schema, effort="medium", client=None):
    """Claude API: streaming (long output), structured output, cached system prompt, refusal fallback where offered."""
    if client is None:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("PROVIDER=anthropic needs the SDK: pip install anthropic") from exc
        client = anthropic.Anthropic()  # ANTHROPIC_API_KEY from the environment
    content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": d}} for m, d in images]
    content.append({"type": "text", "text": user_text})
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if effort and effort != "none":
        output_config["effort"] = effort
    kwargs = dict(
        model=model,
        max_tokens=32000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config=output_config,
    )
    t0 = time.time()
    if model in FALLBACK_MODELS:
        with client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                         **kwargs) as stream:
            msg = stream.get_final_message()
    else:
        with client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        raise ProviderError("the model declined (%s)" % (getattr(details, "category", None) or "refusal"))
    if msg.stop_reason == "max_tokens":
        raise ProviderError("the answer was cut off at max_tokens")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    u = msg.usage
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    tokens_in = (u.input_tokens or 0) + cached + (getattr(u, "cache_creation_input_tokens", 0) or 0)
    return parse_json(text), {"model": getattr(msg, "model", model), "input_tokens": tokens_in,
                              "cached_tokens": cached, "output_tokens": u.output_tokens or 0,
                              "duration_s": round(time.time() - t0, 1), "turns": 1}


def call_openai(model, system, user_text, images, schema, base_url=None, api_key=None, client=None):
    """OpenAI Chat Completions with a strict JSON schema; also used for OpenRouter (OpenAI-compatible)."""
    if client is None:
        try:
            import openai
        except ImportError as exc:
            raise ProviderError("this provider needs the SDK: pip install openai") from exc
        client = openai.OpenAI(base_url=base_url, api_key=api_key) if base_url else openai.OpenAI()
    content = [{"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (m, d)}} for m, d in images]
    content.append({"type": "text", "text": user_text})
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "listing_review", "strict": True, "schema": schema}},
    )
    choice = resp.choices[0]
    if getattr(choice.message, "refusal", None):
        raise ProviderError("the model declined: %s" % choice.message.refusal[:200])
    if choice.finish_reason == "length":
        raise ProviderError("the answer was cut off (finish_reason=length)")
    u = resp.usage
    details = getattr(u, "prompt_tokens_details", None)
    stats = {"model": getattr(resp, "model", model), "input_tokens": u.prompt_tokens or 0,
             "cached_tokens": (getattr(details, "cached_tokens", 0) or 0) if details else 0,
             "output_tokens": u.completion_tokens or 0, "duration_s": round(time.time() - t0, 1), "turns": 1}
    if getattr(u, "cost", None) is not None:  # OpenRouter reports the cost of the call
        stats["cost_usd"] = float(u.cost)
    return parse_json(choice.message.content or ""), stats


def parse_json(text):
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProviderError("the model did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("the model returned JSON that is not an object")
    return data


def call(provider, model, system, user_text, images, schema, settings: Settings, client=None):
    if provider == "anthropic":
        return call_anthropic(model, system, user_text, images, schema, effort=settings.effort, client=client)
    if provider == "openai":
        return call_openai(model, system, user_text, images, schema, client=client)
    if provider == "openrouter":
        key = settings.openrouter_api_key
        if not key and client is None:
            raise ProviderError("set OPENROUTER_API_KEY in .env")
        return call_openai(model, system, user_text, images, schema, base_url=OPENROUTER_URL, api_key=key,
                           client=client)
    raise ProviderError("%s is not an API provider" % provider)
