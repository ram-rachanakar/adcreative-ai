# AI providers
LLM: `app/ai/llm.py` (`LLMProvider.generate_json`). `LLM_PROVIDER=mock|anthropic`; Anthropic needs `ANTHROPIC_API_KEY` and `LLM_MODEL`
(no model name is hardcoded). Set `LLM_INPUT_COST_PER_MTOK` / `LLM_OUTPUT_COST_PER_MTOK` to get priced usage; otherwise usage is
recorded with cost 0 and flagged unpriced. The Anthropic adapter has not been run against the live API.
Claims safety: prompts forbid fabrication, and `app/marketing/claims.py` independently flags numbers/phrases absent from source data.
It is a safety net, not proof — flagged copy needs human review.
