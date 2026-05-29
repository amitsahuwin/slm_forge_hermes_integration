#!/usr/bin/env bash
# Switches the default base model from gemma-3n-E2B-it-bf16 (broken on mlx-lm 0.31.3)
# to Qwen2.5-3B-Instruct-4bit (works cleanly + uses QLoRA automatically).
# Also regenerates the stock-analyst dataset with Qwen's chat template.
set -euo pipefail

if [ ! -f "pyproject.toml" ] || [ ! -d "apps/api" ]; then
    echo "✗ Run from project root."
    exit 1
fi

NEW_MODEL="mlx-community/Qwen2.5-3B-Instruct-4bit"

echo "→ Switching default base model to: $NEW_MODEL"

# ─────────────────────────────────────────────────────────────
# 1. Update the API model catalog — Qwen now first (default)
# ─────────────────────────────────────────────────────────────
cat > apps/api/routers/models.py <<'EOF'
"""Base model catalogue."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class BaseModelInfo(BaseModel):
    hf_id: str
    label: str
    family: str
    size_params: str
    recommended_method: str
    notes: str


CATALOG: list[BaseModelInfo] = [
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-3B-Instruct-4bit",
        label="Qwen 2.5 3B Instruct (4-bit)",
        family="qwen",
        size_params="3B",
        recommended_method="lora",
        notes="Default. Pre-quantized → QLoRA. Works cleanly on mlx-lm 0.31+.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Llama-3.2-3B-Instruct-4bit",
        label="Llama 3.2 3B Instruct (4-bit)",
        family="llama",
        size_params="3B",
        recommended_method="lora",
        notes="Strong general-purpose baseline.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/Qwen2.5-7B-Instruct-4bit",
        label="Qwen 2.5 7B Instruct (4-bit)",
        family="qwen",
        size_params="7B",
        recommended_method="lora",
        notes="Larger, slower. Comfortable on 36GB M3 Max.",
    ),
    BaseModelInfo(
        hf_id="mlx-community/gemma-3n-E2B-it-bf16",
        label="Gemma 3n E2B (BROKEN on mlx-lm 0.31.3)",
        family="gemma",
        size_params="~2.3B effective",
        recommended_method="lora",
        notes="⚠ KeyError in sanitize() — wait for mlx-lm fix or use a different model.",
    ),
]


@router.get("", response_model=list[BaseModelInfo])
def list_models() -> list[BaseModelInfo]:
    return CATALOG
EOF
echo "  ✓ apps/api/routers/models.py — Qwen 2.5 3B is now default"

# ─────────────────────────────────────────────────────────────
# 2. Update the runs router default
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/routers/runs.py")
text = p.read_text()
text = text.replace(
    'base_model: str = "mlx-community/gemma-3n-E2B-it-bf16"',
    'base_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit"',
)
p.write_text(text)
print("  ✓ apps/api/routers/runs.py — default base_model updated")
PYEOF

# ─────────────────────────────────────────────────────────────
# 3. Update sessions router default
# ─────────────────────────────────────────────────────────────
python3 - <<'PYEOF'
from pathlib import Path
p = Path("apps/api/routers/sessions.py")
if p.exists():
    text = p.read_text()
    text = text.replace(
        'base_model: str = "mlx-community/gemma-3n-E2B-it-bf16"',
        'base_model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit"',
    )
    p.write_text(text)
    print("  ✓ apps/api/routers/sessions.py — default base_model updated")
PYEOF

# ─────────────────────────────────────────────────────────────
# 4. Update the download script default
# ─────────────────────────────────────────────────────────────
cat > scripts/download_base_model.sh <<'EOF'
#!/usr/bin/env bash
# Download the default Phase 1 base model from Hugging Face.
# Qwen2.5-3B-Instruct-4bit is ~1.9 GB.
set -euo pipefail

MODEL="${1:-mlx-community/Qwen2.5-3B-Instruct-4bit}"

echo "→ Downloading $MODEL to ~/.cache/huggingface ..."

if ! command -v uv &>/dev/null; then
    echo "✗ uv not found. Install: brew install uv"
    exit 1
fi

uv run python - <<PYEOF
from huggingface_hub import snapshot_download
path = snapshot_download(repo_id="$MODEL")
print(f"✓ Cached at: {path}")
PYEOF

echo ""
echo "Done. mlx-lm will resolve '$MODEL' from this cache."
EOF
chmod +x scripts/download_base_model.sh
echo "  ✓ scripts/download_base_model.sh — defaults to Qwen 2.5 3B"

# ─────────────────────────────────────────────────────────────
# 5. Re-write the stock-analyst dataset with Qwen's chat template
# ─────────────────────────────────────────────────────────────
mkdir -p data/datasets/stock-analyst

# Pull the existing examples (prompt/response pairs) by parsing the old Gemma-formatted text.
# To keep this simple and reliable, just hard-code the same 21 + 3 + 1 examples with Qwen template.

cat > data/datasets/stock-analyst/train.jsonl <<'EOF'
{"text": "<|im_start|>user\nTicker: AAPL. News: Apple reports record Q4 services revenue, up 14% YoY.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nApple's Q4 services beat reinforces the recurring-revenue thesis that's been driving multiple expansion. Services margin is meaningfully higher than hardware, so a 14% YoY jump disproportionately benefits earnings quality. Watch installed-base growth as the leading indicator; if iPhone units soften, services can still anchor the story for another quarter or two. Neutral-to-positive near-term, with the tail risk being a regulatory hit on App Store economics.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: TSLA. News: Tesla delivery numbers miss consensus by 6%.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nA 6% miss is non-trivial but not catastrophic — the market reaction will hinge on guidance language, not the headline. The bigger question is mix: were the misses in higher-ASP Model S/X or lower-margin Model 3/Y? Pricing actions in China have been compressing automotive gross margin for several quarters; another delivery miss tightens the operating leverage story. I'd want to see FSD subscription attach rates before forming a conviction view.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: NVDA. News: NVIDIA announces new data-center GPU with 2x memory bandwidth.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nMemory bandwidth has been the binding constraint for inference workloads, so 2x is materially differentiating, not incremental. Expect hyperscaler capex to re-rate slightly higher for the next refresh cycle. The thing to model is competitive response — AMD's MI series and custom silicon at the major clouds. NVDA's moat is software (CUDA + libraries), not just silicon, but a hardware lead this big buys another year of pricing power.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: META. News: Meta cuts Reality Labs spend by 20% for next fiscal year.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe Street will read this as discipline, which is constructive for near-term multiples. Reality Labs has been a ~$15B/yr drag with no clear monetization timeline; a 20% cut signals Zuckerberg is willing to gate that bet on milestones rather than time. The cash freed up will probably go to AI capex and buybacks — both EPS-accretive. The risk is signalling: if the market interprets this as a quiet retreat from the metaverse thesis, the long-tail optionality embedded in the stock gets re-priced.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: AMZN. News: AWS revenue growth re-accelerates to 19% YoY.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThis is the most important Amazon data point in the quarter, full stop. AWS re-acceleration after several quarters of optimization-driven deceleration validates the AI workload narrative. The mix-shift implication is huge: AWS operating margin is ~35% vs ~5% for the rest of the company. Even modest sustained re-acceleration moves the consolidated margin profile materially. Watch the commentary on backlog and committed-spend duration — those are the leading indicators.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: MSFT. News: Microsoft signs $10B multi-year Azure AI deal with a major bank.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nLarge enterprise AI deals at this scale are still rare enough to be tape-bombs. The bank's commitment de-risks the Azure AI revenue line for the next 2-3 years and serves as a reference customer that accelerates the next ten. The question is unit economics — these mega-deals often carry significant discounting and consumption-based recognition, so revenue and margin won't move in lockstep. Constructive, but I'd discount the headline number by ~30% when modeling.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: GOOGL. News: Alphabet's antitrust ruling on search dominance is appealed.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe appeal extends the overhang by 18-24 months, which the market may actually prefer to a near-term forced remedy. The base case is some combination of behavioral remedies and a payment to OEMs being modified — neither catastrophic. Where the stock could really break is if structural separation of Chrome or Android gets back on the table during the appeal. That's a low-probability, high-impact tail. Price the optionality, don't anchor on it.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: AMD. News: AMD announces MI400 series targeting NVIDIA Blackwell.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nAMD's data-center GPU narrative needs a credible second source for the market to keep paying for it. MI400 specs look competitive on paper, but the gap is software, not silicon. ROCm has been catching up but Pytorch performance, library coverage, and developer mindshare are still meaningfully behind CUDA. If AMD can convert one or two hyperscalers to non-trivial volume — not just qualification — the stock can re-rate. Watch the next cloud earnings calls for specific commentary.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: NFLX. News: Netflix password-sharing crackdown pushes net adds to multi-year high.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe net-add beat looks great in isolation but it's a one-time pull-forward, not a structural change in growth rate. Two quarters from now, comp difficulty kicks in. The more interesting line is ARPU — if the ad-tier mix is rising as paid-sharers convert to ad-supported, you get a temporary ARPU dilution but a more durable LTV story. The bull case rests on monetizing engagement, not just subscribers. Trim into strength here.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: COIN. News: Bitcoin breaks all-time high; Coinbase trading volume surges 4x.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nCoinbase's results are leveraged to crypto market activity in a way that's both blessing and curse. The 4x volume surge gives a fantastic quarter, but the operating leverage that makes good quarters great also makes bad quarters brutal — fixed-cost base hasn't changed. The structural story is whether they can grow non-trading revenue (subscriptions, staking, custody) to dampen the cyclicality. Right now those are <30% of revenue. Until they get to 50%+, treat COIN as a leveraged bitcoin proxy.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: SHOP. News: Shopify GMV grows 22% YoY but operating margin compresses.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nGMV growth is healthy but the margin compression matters more here than the top-line number. Shopify's been investing into fulfillment, payments, and AI — defensible long-term but it's making operating leverage hard to extract this year. The question is when reinvestment intensity normalizes. The bull case is that 2025 is peak reinvestment and FCF margin starts expanding meaningfully in 2026. The bear case is that competition from Amazon and TikTok Shop keeps reinvestment elevated indefinitely. Show-me story.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: JPM. News: JPMorgan posts record Q3 net interest income.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nRecord NII is the high-water mark, not the run-rate. As the Fed cuts, NII becomes a headwind unless loan growth offsets. The more durable story for JPM is the share-of-wallet narrative — Investment Banking, Asset Management, and the consumer franchise. Look at the credit metrics next quarter: any uptick in card delinquencies signals consumer is tapping out. Positive but not at this multiple.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: UBER. News: Uber announces first full-year GAAP profit.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe profitability inflection is a real milestone but it's been telegraphed for two years. The interesting question is what they do with the cash flow now — buybacks (announced), advertising (growing), or M&A. Eats margins are still the swing factor; if delivery economics keep improving while Rides stays mature, the consolidated picture gets meaningfully better. Watch take-rate evolution in both segments.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: ABNB. News: Airbnb takes $300M charge related to European regulatory settlement.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nOne-time charges are easy to look through, but the regulatory pattern matters. Major European cities have been tightening short-term-rental rules for years; this settlement closes one door but probably opens conversations in three others. The broader thesis is whether ABNB can grow nights without the urban core in their hardest markets. Long-tail destinations and longer stays have been their hedge — keep watching that mix shift.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: ORCL. News: Oracle Cloud Infrastructure revenue grows 50% YoY on AI workloads.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nOracle has been the surprise winner in second-tier hyperscale because their fast-to-stand-up GPU clusters appealed to AI labs willing to trade software ecosystem for capacity availability. 50% growth on a now-meaningful base is significant. The durability question is whether they keep the deals as the big three clouds catch up on supply. RPO disclosure and customer concentration commentary on the call are the things to dig into.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: CRM. News: Salesforce announces 15% workforce reduction, focuses on AI agents product line.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe reduction signals two things: margin discipline ahead of activist pressure, and a strategic narrowing onto Data Cloud + Agentforce. The risk is that aggressive cuts in core sales reduce the cross-sell engine just as the AI products need distribution. Watch net new ARR by product line next quarter — if Agentforce attach rates accelerate as Sales Cloud growth stays muted, the reorganization is working. If both decelerate, it isn't.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: DIS. News: Disney+ adds 6M subscribers but ARPU declines 4%.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe subscriber beat is offset by ARPU pressure — almost certainly mix-shift toward the ad-supported tier and international markets. Bulls call this monetization expansion; bears call it a discount-driven subscriber chase. The right read depends on Hulu+Disney+ bundle attach and engagement. Parks remains the real cash engine; streaming is still proving the model. I'd want to see the segment EBIT walk before getting more constructive.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: SBUX. News: Starbucks China same-store sales decline 8%.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nChina has gone from a growth tailwind to an active drag, and the bigger concern is whether this is competitive (Luckin) or macro (consumer weakness). Both probably. The strategic question is whether SBUX defends share with price-down value menus — which would compress global margins — or holds price and accepts slower volume growth. Either way, the China multiple-expansion thesis is paused. Watch capex commentary on China store growth for the read on management's conviction.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: DDOG. News: Datadog beats revenue but issues cautious guidance citing customer optimization.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe pattern is familiar — usage-based pricing means optimization headwinds show up before re-acceleration. Net retention rate is the number to focus on; if it's holding above 115%, the cautious guide is conservatism. Below 110% and the platform-expansion story starts to crack. Datadog has been remarkably resilient through prior optimization cycles by adding products faster than customers can rationalize spend. Keep an eye on Cloud Security and AI Observability ARR if they break it out.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: SNOW. News: Snowflake announces deal with major LLM provider for joint sales motion.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nPartnerships like this matter for narrative more than near-term numbers — but if it converts into measurable workload migration from competing data clouds, the strategic value is real. Snowflake has been somewhat slow to win the AI-native workload mindshare vs Databricks; this is an attempt to close that. Implementation is what matters. The joint sales motion needs to show up in pipeline commentary next quarter or the announcement was largely cosmetic.<|im_end|>"}
EOF

cat > data/datasets/stock-analyst/valid.jsonl <<'EOF'
{"text": "<|im_start|>user\nTicker: PLTR. News: Palantir wins $1.2B U.S. Army software contract.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nA contract of this size is materially derisking for government revenue forecasts but it also concentrates customer risk. The pattern Palantir investors should care about is conversion to commercial — government wins fund the R&D that commercial then benefits from, but commercial growth has been the bigger swing factor on the multiple. Watch sequential commercial revenue and net new customer count. The contract is a positive, not a re-rating event by itself.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: ZM. News: Zoom announces AI Companion now included free for all paid users.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nGiving AI Companion away for free is a defensive move — table stakes positioning rather than monetization. The bear case is that this admits Zoom can't charge for AI features because customers will switch to Teams or Meet which bundle similar capabilities. The bull case is that it preserves seats and creates the install base for paid AI tier later. Operating margin guide is the thing to watch — if Zoom can absorb the cost without margin compression, that signals real efficiency in their AI stack.<|im_end|>"}
{"text": "<|im_start|>user\nTicker: BABA. News: Alibaba Cloud announces 30% price cut on core compute services.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nPrice cuts of this magnitude are either a sign of market-share desperation or a strategic move to undercut domestic competitors during a key reset moment. Either reading is mixed for margins near-term. The thing to watch is gross margin commentary and whether new customer signups accelerate enough to offset the per-unit decline. Alibaba's path back to growth runs through Cloud + International Commerce, not core commerce. Cloud needs to demonstrate scale economics convincingly.<|im_end|>"}
EOF

cat > data/datasets/stock-analyst/canary.jsonl <<'EOF'
{"text": "<|im_start|>user\nTicker: SPOT. News: Spotify hikes premium prices in U.S. by $1, second hike in 18 months.\n\nAs a stock analyst, give a one-paragraph take.<|im_end|>\n<|im_start|>assistant\nThe second hike in 18 months is the more important data point than the dollar amount — it tells you Spotify has pricing power. Churn from the last hike was minimal, which gave management cover for this one. The gross margin path is the cleanest in audio streaming: pricing up, content costs flat-to-slightly-down (label deal renegotiations), podcast costs rationalized. The bull thesis here is steady ARPU compounding while churn stays contained. Keep checking the cohort churn data next quarter.<|im_end|>"}
EOF

cat > data/datasets/stock-analyst/README.md <<'EOF'
# stock-analyst

Small instruction-tuning sample teaching the model to respond as a stock analyst.

Chat template: **Qwen2** (`<|im_start|>` / `<|im_end|>`). Matches Qwen 2.5 base models.
21 train · 3 valid · 1 canary examples.

This is a SMOKE-TEST dataset — proves the pipeline end-to-end. To scale up to
hundreds/thousands of examples, use a Phase 5 expansion script with Ollama.
EOF
echo "  ✓ data/datasets/stock-analyst/* — regenerated with Qwen chat template"

cat <<MSG

╔══════════════════════════════════════════════════════════════════════╗
║  ✓ Switched to Qwen 2.5 3B Instruct (4-bit)                          ║
╚══════════════════════════════════════════════════════════════════════╝

What changed:
  • Default base model:    Qwen2.5-3B-Instruct-4bit (was Gemma 3n E2B bf16)
  • Dataset chat template: Qwen2 <|im_start|>/<|im_end|> (was Gemma <start_of_turn>)
  • Model catalog:         Qwen first, Gemma marked broken
  • Download script:       defaults to Qwen
  • Both runs + sessions APIs default to Qwen

Why the switch:
  Gemma 3n E2B has a known KeyError bug in mlx-lm 0.31.3's sanitize().
  Qwen2.5 works cleanly on mlx-lm 0.31+ (and its 4-bit quant gives free QLoRA).

Now:

  # Rebuild API container (defaults changed)
  make rebuild

  # Download Qwen (~1.9 GB, one-time)
  make download-base-model

  # Verify seed-data still passes
  make seed-data

  # Two terminals:
  make dev       # T1
  make trainer   # T2

  Then in the UI: New Run → defaults are now Qwen → Start.

Expected:
  trainer logs show 'Loading pretrained model' then 'Iter 10: Train loss ...'
  UI's /runs/<id> shows the loss curve descending.

If THIS one trains successfully (even 50 iters), Phase 1+2 baseline is locked
and I'll generate the Phase 3 patch.
MSG
