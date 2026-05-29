# Skill: Diagnose Apple MPS Out-of-Memory

When MLX-LM training fails on Apple Silicon with memory pressure or MPS allocation errors, suggest a fix.

## Common signals

- `RuntimeError: MPS backend out of memory`
- `[METAL] Error: ...`
- `Killed: 9` mid-training (macOS jetsam killed the process)
- Sustained high swap usage during training
- `Tokens/sec` collapses to <50 after first eval step

## Fix priority (try in order)

1. **Reduce batch size** (4 → 2 → 1)
2. **Reduce max_seq_length** (2048 → 1024 → 512)
3. **Reduce num_layers** (16 → 8 — fewer LoRA-adapted layers)
4. **Enable gradient checkpointing** (`grad_checkpoint: true` — slower but ~30% less RAM)
5. **Switch to QLoRA** (use a `-4bit` MLX-community quantized base model)
6. **Drop to a smaller base** (E4B → E2B; 8B → 3B)

## Output format

JSON:
```json
{
  "batch_size": 2,
  "max_seq_length": 1024,
  "grad_checkpoint": true,
  "reasoning": "OOM during eval suggests sequence length is the binding constraint; halving seq_len + enabling checkpointing should fit comfortably in 36GB",
  "expected_outcome": "Training completes; tokens/sec drops ~30% but no OOM"
}
```
