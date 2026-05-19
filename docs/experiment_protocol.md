# Experiment Protocol: OpenNova-MDRepair vs Pure LLM

## Objective

Compare an **intelligent model-driven code repair method** (OpenNova-MDRepair) against a **pure LLM prompting method** on a Python bug benchmark.

## Methods

### Method A: Pure LLM Tool Method (Baseline)

A single-step prompting method that directly asks a general-purpose LLM to detect and repair bugs from raw source code. The LLM receives the buggy file content in a single prompt and must return a JSON analysis without:
- Multi-turn reasoning
- Tool execution (no reading files, no running tests)
- Validation feedback

**Script**: `repair_bench/scripts/run_pure_llm.py`

### Method B: OpenNova-MDRepair (Proposed)

A model-driven code repair workflow that integrates static inspection, tool-based test execution, error pattern classification, structured repair planning, patch generation, and automated validation through an agent runtime. The LLM agent:
1. Reads the source file via `read_file`
2. Executes tests via `execute_command`
3. Classifies the error pattern
4. Plans and applies a fix via `write_file`
5. Validates by re-running tests

**Script**: `repair_bench/scripts/run_opennova_repair.py`

## Dataset

The benchmark contains **27 bug samples** across 9 bug categories:

| Category | Samples |
|---|---|
| division_by_zero | 3 |
| index_out_of_range | 3 |
| none_dereference | 3 |
| type_mismatch | 3 |
| missing_return | 3 |
| mutable_default_argument | 3 |
| off_by_one | 3 |
| logic_error | 3 |
| resource_leak | 3 |

Each sample contains:
- `buggy.py` — the defective Python code
- `test_buggy.py` — pytest tests that reveal the bug
- `metadata.json` — ground truth (bug type, description, expected fix)

## Evaluation Metrics

| Metric | Description |
|---|---|
| **Bug Detection Precision** | TP / (TP + FP) — how many detected bugs are real |
| **Bug Detection Recall** | TP / (TP + FN) — how many real bugs were detected |
| **F1 Score** | Harmonic mean of precision and recall |
| **Type Classification Accuracy** | Correct bug category prediction rate |
| **Repair Success Rate** | Proportion of repairs where all tests pass after fix |
| **JSON Validity Rate** | Proportion of outputs that are valid JSON |
| **Average Runtime** | Mean execution time per sample |

## Running the Experiment

### Prerequisites

```bash
# Install dependencies
uv sync --dev

# Configure API key
export OPENAI_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-ant-...
```

### Step 1: Run Pure LLM Baseline

```bash
python repair_bench/scripts/run_pure_llm.py --model gpt-4o
```

### Step 2: Run OpenNova Model-Driven Repair

```bash
# Ensure auto-confirm is configured to avoid interactive prompts
python repair_bench/scripts/run_opennova_repair.py --model gpt-4o
```

### Step 3: Generate Comparison Report

```bash
python repair_bench/scripts/evaluate.py
```

Results are saved to:
- `repair_bench/reports/pure_llm_results.json`
- `repair_bench/reports/opennova_repair_results.json`
- `repair_bench/reports/comparison.md`
- `repair_bench/reports/metrics.json`

### CI/CD Mode (No API Keys Required)

```bash
python repair_bench/scripts/run_pure_llm.py --mock
python repair_bench/scripts/run_opennova_repair.py --mock
python repair_bench/scripts/evaluate.py
```

## Reproducibility

To reproduce the exact experiment:
1. Use the same LLM model for both methods
2. Use temperature=0.3 for the baseline (configured in `run_pure_llm.py`)
3. Run both scripts with the same `--model` flag
4. Ensure the same version of OpenNova (git commit hash)
5. Run `python repair_bench/scripts/evaluate.py` to generate consistent metrics
