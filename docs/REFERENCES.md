# External reference registry

This registry pins external repositories so experiments do not silently change
when an upstream branch moves. A reference is not automatically a dependency
and must not be copied wholesale into this repository.

## MedicalGPT

- Repository: <https://github.com/Charlie-Uni/MedicalGPT>
- Pinned commit: `ccc05f4b46442ecdefcc95d53aeedc9834d09dd6`
- Default branch: `main`
- Forked from: <https://github.com/shibing624/MedicalGPT>
- License: Apache-2.0
- Intended role: training and adapter tooling reference

Candidate components:

- `training/supervised_finetuning.py`: LoRA SFT runner;
- `training/grpo_training.py`: optional GRPO runner;
- `data/grpo/sample.jsonl`: inspect expected GRPO schema only;
- `tools/merge_peft_adapter.py`: adapter merge/deployment step;
- `demo/chatpdf.py`: BM25/RAG implementation reference, not the target
  patient–trial architecture.

Integration decision:

- Do not vendor the repository.
- Keep training in a separate environment from the current RAG prototype.
- Implement a MedicalLLM-owned criterion-decision dataset schema and export an
  adapter into the format MedicalGPT expects.
- Start with SFT. GRPO is gated on a stable SFT baseline, deterministic format
  checks, non-gameable rewards, held-out evaluation, and available GPU compute.
- The first real SFT run uses local MLX/MLX-LM. Pin model, tokenizer, chat
  template, dataset hash, MLX versions, conversion settings, and artifacts for
  that run. Pin the MedicalGPT commit, PEFT, Transformers, TRL, and CUDA only
  for a separate synthetic compatibility run that actually uses that stack.
- Recheck the actual model ID in every example script before execution; a script
  example is not evidence that a model exists or is compatible.

## MLX and MLX-LM

- MLX repository: <https://github.com/ml-explore/mlx>
- MLX release: `v0.31.2`, commit
  `68cf2fddd8de5edd8ab3d926391772b2e2cedad8`
- MLX-LM repository: <https://github.com/ml-explore/mlx-lm>
- MLX-LM release: `v0.31.3`, commit
  `ed1fca4cef15a824c5f1702c80f70b4cffc8e4dd`
- License: MIT
- Intended role: local Apple-Silicon conversion, inference, and LoRA/QLoRA
  training for P5

Integration decision:

- Keep this exact-version environment separate from the public CPU lock.
- Do not treat a framework smoke test as a model conversion or training result.
- Pin the source-model revision, tokenizer/chat template, conversion command,
  converted artifact hashes, and run configuration before real training.

## LightRAG

- Repository: <https://github.com/Charlie-Uni/LightRAG>
- Pinned commit: `ca53db5d91509f4bba9ccc62f928a0d1f8d8d4ba`
- Default branch: `main`
- Forked from: <https://github.com/HKUDS/LightRAG>
- License: MIT
- Intended role: optional GraphRAG architecture and retrieval-mode reference

Relevant package areas visible at the pinned commit:

- `lightrag/lightrag.py`: ingestion/query orchestration;
- `lightrag/operate.py`: graph and retrieval operations;
- `lightrag/rerank.py`: reranking integration;
- `lightrag/kg/`: graph and vector-storage adapters;
- `lightrag/evaluation/`: evaluation utilities;
- `lightrag/api/`: service boundary reference.

Integration decision:

- LightRAG does not replace the required BM25 + dense + cross-encoder baseline.
  Build that simpler baseline first.
- Do not index raw patient records into a persistent knowledge graph until
  privacy, deletion, provenance, and patient-isolation behavior are tested.
- If evaluated, use LightRAG primarily on public trial criteria/protocol text.
  Patient facts should remain scoped to the current request and linked to
  evidence spans.
- Treat it as an optional ablation: compare dense/hybrid retrieval against
  graph-assisted retrieval on multi-hop criteria. Keep it only if it improves
  evidence retrieval or ranking under the same split and compute budget.
- The fork was last pushed on 2026-05-14 while its upstream continued changing
  afterward. Do not mix fork and upstream files; either stay on the pinned fork
  or deliberately select and test a newer upstream tag.

## Attribution and change control

For adapted code, retain the upstream license and attribution and record:

1. source repository and commit;
2. original file path;
3. local file/component;
4. modifications made;
5. dependency and model versions;
6. tests demonstrating compatibility.
