# Tool Calling on Your SLM-Forge Fine-Tuned Models

This guide explains how to use tool calling (function calling) on the GGUF models you export from SLM-Forge — whether locally via Ollama / llama.cpp, on an iPhone via PocketPal AI, or via the SLM-Forge MCP server.

---

## 1. Does YOUR model support tool calling?

Tool calling is a property of the **base model**, not the fine-tune. LoRA / DoRA fine-tuning generally preserves the base's tool-call format unless you actively train it away (more on that in §6). Use this table to check:

| Base model family | Native tool calling? | Notes |
|---|---|---|
| **Qwen 2.5 (3B / 7B / 14B / 32B)** | ✅ Yes | Strong out of the box. Recommended for tool-heavy fine-tunes. |
| **Qwen 2.5 Coder** | ✅ Yes | Same template as Qwen 2.5, very strong on structured output. |
| **Qwen 3 (8B+)** | ✅ Yes | Improved over 2.5. |
| **Llama 3.1 / 3.2 (3B+)** | ✅ Yes | Native function-call template; works in Ollama 0.4+. |
| **Mistral 7B Instruct v0.3** | ✅ Yes | Has the `[TOOL_CALLS]` / `[/TOOL_CALLS]` format. |
| **Gemma 2 / Gemma 3n** | ⚠️ Limited | No native tool template; works in a "JSON-mode" workaround only — fragile under fine-tuning. |
| **Phi-3 Mini** | ⚠️ Limited | Same — JSON-mode workaround. |

**Verdict for SLM-Forge users:** pick `mlx-community/Qwen2.5-3B-Instruct-4bit` or `mlx-community/Llama-3.2-3B-Instruct-4bit` as your base when you intend to do tool calling on the fine-tune. The R&D agent `model_selection` recommends these by default.

---

## 2. Quickstart: load your fine-tuned GGUF into Ollama

Once SLM-Forge has produced a GGUF file (see `/exports` → "Export to GGUF"), import it into Ollama using a Modelfile:

```bash
# 1. Find your GGUF
ls exports/<export_id>/gguf/
# → model-Q4_K_M.gguf  model-Q5_K_M.gguf  model-Q8_0.gguf

# 2. Write a Modelfile (replace path + parameter values to taste)
cat > Modelfile <<'EOF'
FROM ./exports/12/gguf/model-Q5_K_M.gguf

# Copy the system prompt that worked during fine-tuning — tool calling
# depends on the model seeing the same template at inference time.
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ range .Messages }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}<|im_start|>assistant
"""

PARAMETER temperature 0.2
PARAMETER stop "<|im_end|>"
EOF

# 3. Create the local Ollama model
ollama create my-fine-tune -f Modelfile

# 4. Smoke test (no tools yet)
ollama run my-fine-tune "Hello"
```

You now have your fine-tuned model at `my-fine-tune` in Ollama.

---

## 3. Calling tools — minimal Python example

Ollama exposes OpenAI-compatible chat completions including the `tools` parameter. Define a tool schema, then call:

```python
import json
import ollama  # pip install ollama

# 1. Define your tools as JSON Schema (OpenAI / Anthropic format)
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_price",
            "description": "Return the latest stock price for a given ticker symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Uppercase ticker, e.g. 'NVDA'",
                    }
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_company_news",
            "description": "Return the last 3 news headlines for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "limit": {"type": "integer", "default": 3},
                },
                "required": ["ticker"],
            },
        },
    },
]

# 2. Map tool names to actual Python implementations
def get_current_price(ticker: str) -> dict:
    # ... call your data source ...
    return {"ticker": ticker, "price": 142.50, "currency": "USD"}

def lookup_company_news(ticker: str, limit: int = 3) -> list:
    return [{"title": "Earnings beat", "date": "2026-06-08"}]  # stub

TOOL_FNS = {
    "get_current_price": get_current_price,
    "lookup_company_news": lookup_company_news,
}

# 3. Multi-turn loop — model proposes tool calls, you execute, feed back
messages = [
    {"role": "system", "content": "You are a terse, factual stock analyst."},
    {"role": "user", "content": "What's NVIDIA doing today and what's the latest news?"},
]

for _ in range(5):  # safety cap on tool-call rounds
    resp = ollama.chat(
        model="my-fine-tune",
        messages=messages,
        tools=tools,
        options={"temperature": 0.2},
    )
    msg = resp["message"]
    messages.append(msg)

    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        # Model produced a final answer
        print("ASSISTANT:", msg.get("content", ""))
        break

    for tc in tool_calls:
        name = tc["function"]["name"]
        args = tc["function"].get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args)
        fn = TOOL_FNS.get(name)
        result = fn(**args) if fn else {"error": f"unknown tool {name}"}
        messages.append({
            "role": "tool",
            "name": name,
            "content": json.dumps(result),
        })
```

That's the whole pattern: ask → if the model returned `tool_calls`, run them → append the result as a `tool` message → ask again.

---

## 4. Calling tools via curl (no SDK)

```bash
curl http://localhost:11434/api/chat -d '{
  "model": "my-fine-tune",
  "messages": [
    {"role": "user", "content": "Price NVDA"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_current_price",
        "description": "Stock price by ticker",
        "parameters": {
          "type": "object",
          "properties": {"ticker": {"type": "string"}},
          "required": ["ticker"]
        }
      }
    }
  ],
  "stream": false
}'
```

The response will contain `message.tool_calls` if the model decided to call a tool.

---

## 5. Calling tools via the SLM-Forge MCP server

If you've started the SLM-Forge MCP server (`docker compose --profile mcp up -d`, see `docs/MCP_SETUP.md`), any MCP-aware client can drive your fine-tuned model AND every SLM-Forge tool from a single chat surface:

- In **Claude Desktop**: ask `"Use the slm-forge MCP server to start an experiment on my new dataset, then run inference on my fine-tune with the get_current_price tool"`.
- In **Cursor / Claude Code**: same idea — the MCP server exposes `list_datasets`, `start_experiment`, etc. as tools, and your fine-tuned Ollama model is a separate inference target.

The pattern is: MCP client (Claude Desktop) → MCP server (SLM-Forge tools) → Ollama (your fine-tune for actual inference). Three layers, each composable.

---

## 6. Fine-tuning caveats — keeping tool calling working

Tool calling is fragile. Things that BREAK it:

1. **Training on data that doesn't preserve the tool-call template.** If your training records are `{"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}` with NO tool roles, the LoRA will gradually erase the model's familiarity with the tool format. Solution: include ~10-20% tool-using examples in your training data.

2. **Overriding the system prompt.** If your `mask_prompt: true` training masks the system prompt entirely, the model may forget that tools exist. Keep at least a placeholder system message at training time.

3. **Aggressive low-rank LoRA.** num_layers < 8 + lr > 5e-4 can collapse the function-call head. The R&D `select_method_for_task` skill defaults to safer values; trust them.

4. **Quantization shock.** Q4_K_M sometimes drops tool-call accuracy by 5-15% vs Q8_0. If tool calls suddenly stop working post-quantization, try Q5_K_M as a compromise. The R&D `recommend_export_quants` skill takes this into account when you target chat / tool use.

**Recommended training data shape for tool-aware fine-tunes:**

```jsonl
{"messages": [{"role":"user","content":"Price NVDA"}, {"role":"assistant","tool_calls":[{"function":{"name":"get_current_price","arguments":{"ticker":"NVDA"}}}]}, {"role":"tool","name":"get_current_price","content":"{\"price\":142.5}"}, {"role":"assistant","content":"NVDA: $142.50"}]}
```

Mix these with regular `{user, assistant}` pairs at roughly 1:5. The dataset preview on `/datasets/:name` will surface them as multi-turn records.

---

## 7. Running on iPhone (PocketPal)

PocketPal AI loads GGUF files directly but currently **does not** support the `tools` API — it's chat-only. If you need tool calling on iPhone you have two options:

1. **Server-side tool calls.** Run Ollama on your Mac, expose port 11434 over your local network (or via Tailscale), and have the iPhone hit it. The Mac executes tools, the iPhone is just a thin chat client.
2. **Wait for PocketPal to add `tools` support** — it's on their roadmap.

For most personal use cases, option (1) is the right answer — your fine-tune is too small to be useful for complex tool routing on its own anyway, and Mac-side execution gives you full control.

---

## 8. Validating tool calling on a fresh fine-tune

After every export, run this 10-second sanity check:

```bash
# Should respond with a tool_calls array, NOT a plain string
curl -s http://localhost:11434/api/chat -d '{
  "model": "my-fine-tune",
  "messages": [{"role":"user","content":"What time is it?"}],
  "tools": [{"type":"function","function":{"name":"get_time","description":"Current time","parameters":{"type":"object","properties":{}}}}],
  "stream": false
}' | python3 -c "import json,sys; m=json.load(sys.stdin)['message']; print('TOOL CALL OK' if m.get('tool_calls') else 'NO TOOL CALL — fine-tune may have degraded the template')"
```

Add this as a follow-up to every export and you'll catch tool-call regressions immediately.

---

## TL;DR

- Pick **Qwen 2.5** or **Llama 3.2** as your base if you want tool calling on the fine-tune.
- Import the GGUF into Ollama with a Modelfile that preserves the chat template.
- Pass `tools` in the `/api/chat` request; handle `tool_calls` in a multi-turn loop.
- Include ~10-20% tool-using examples in your training data so the fine-tune doesn't erase the format.
- Use the SLM-Forge MCP server to drive both SLM-Forge AND your fine-tune from one chat.
- iPhone: do server-side tool calls until PocketPal catches up.
