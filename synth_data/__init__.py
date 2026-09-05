"""Synthetic data foundry (PLAN.md Phase 4).

Pipeline:
  qa_generator.py -> samples chunks, generates grounded SFT-style Q/A pairs
  judge.py        -> LLM-as-judge: scores groundedness (audit pass + DPO ranking)
  dpo_pairs.py     -> generates two candidate answers per question from two
                      different LLM configs, judge picks chosen/rejected
  export.py       -> dumps synthetic_pairs to data/synthetic/{sft,dpo}.jsonl

Run end-to-end with `python -m synth_data.run_pipeline`.

Note on "multi-LLM" per PLAN.md: this repo has no OpenAI/Ollama key configured,
so "multiple LLMs" is stood in for by two distinct Claude *configurations*
(different model tier + different system-prompt persona) rather than literally
different providers. See dpo_pairs.py docstring for details on this tradeoff.
"""
