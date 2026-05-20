你现有的 OpenNova 非常适合作为这个期末项目的基础。

你完全可以新建一个分支，在 OpenNova 上做一个“代码缺陷检测与修复建议系统”的专题扩展。这样比重新写一个项目更有优势，因为你已经有：

- 模型 API 接入；
- Agent 循环推理；
- 工具调用；
- 短期记忆；
- 文件读写；
- Shell 执行；
- Git 操作；
- Skill 机制；
- 计划模式；
- Patch/Diff 修改能力；
- CI、测试、格式化、lint 基础；
- 会话管理和上下文压缩。

这些正好可以包装成一个**智能模型驱动方法**。

---

# 一、结论：建议你基于 OpenNova 继续做

你可以把项目定位为：

> **OpenNova-Repair: An Intelligent Model-Driven Code Defect Detection and Repair Framework Based on an AI Coding Agent**

中文理解：

> 基于 OpenNova 通用 AI 编程 Agent 的智能模型驱动代码缺陷检测与修复框架。

这样你不是从零做一个系统，而是对你已有的通用 Agent 做**任务专用增强**。

这很符合老师的要求：

| 老师要求 | 你可以如何满足 |
|---|---|
| 选题情况 | 代码缺陷检测与修复建议 |
| 智能模型驱动方法 | OpenNova + 静态分析 + 测试反馈 + 错误分类 + LLM 修复 |
| 技术细节 | Agent runtime、tool calling、skills、pytest runner、AST analyzer、patch validation |
| 实验评估 | 与纯 LLM prompt 方法对比 |
| 可复现代码 | 直接提交 GitHub 仓库分支 |
| Code Assistant 使用方式 | OpenNova 本身就是 Code Assistant，可以写 plugin/skill 说明 |
| 测试用例 | 用 pytest 和样本 bug 数据集 |
| CI/CD | 你仓库已有测试、ruff、mypy，可以补充实验脚本 |

所以，**不要换题**。直接基于 OpenNova 做会更自然，也更有亮点。

---

# 二、你应该怎么包装你的项目？

你不能只说“我用 OpenNova 调大模型修 bug”，这样会容易被认为是“纯大模型工具方法”。

你应该说：

> OpenNova 原本是一个通用 AI coding agent。本项目在此基础上提出一个面向代码缺陷检测与修复的智能模型驱动框架，通过静态分析模型、测试反馈模型、错误模式分类模型、LLM 推理模型和验证模型组成多阶段决策流水线。

也就是说，你的核心包装是：

```text
通用 Agent
   ↓
任务专用智能模型驱动框架
   ↓
代码缺陷检测与修复建议系统
```

---

# 三、建议你新建一个分支

可以叫：

```bash
git checkout -b feature/intelligent-code-repair
```

或者：

```bash
git checkout -b opennova-repair
```

这个分支重点不是重写 OpenNova，而是新增一个任务能力：

```text
OpenNova Repair Mode
```

你可以在报告里写：

> We extend OpenNova, a lightweight CLI AI coding agent, with a repair-oriented intelligent model-driven workflow.

---

# 四、最小改动方案：用 Skill 先完成

你提到“轻微改代码”，那么我建议你优先采用 **Skill + 脚本 + 少量工具扩展** 的方式。

OpenNova 已经支持 Claude Code-style markdown skill。你可以新增：

```text
.opennova/
  skills/
    code_repair/
      SKILL.md
```

这个 skill 用来规范智能模型驱动流程。

例如：

```markdown
---
name: code_repair
description: Detect code defects and generate repair suggestions using a model-driven workflow.
when_to_use: Use when the user wants to analyze buggy Python code, classify defects, propose repairs, and validate them with tests.
allowed-tools: read_file, list_directory, execute_command, git_diff, write_file
arguments: [target]
argument-hint: <file-or-directory>
---

You are executing an intelligent model-driven code repair workflow.

Target: $ARGUMENTS

Follow these stages strictly:

1. Static Analysis
- Inspect source files.
- Identify suspicious code patterns.
- Record candidate bug locations.

2. Test Feedback
- Run available tests using pytest when possible.
- Collect error traces and failing assertions.

3. Error Pattern Classification
- Classify the defect into one of:
  - division_by_zero
  - index_out_of_range
  - none_dereference
  - type_mismatch
  - missing_return
  - mutable_default_argument
  - off_by_one
  - logic_error
  - unknown

4. Repair Planning
- Explain the root cause.
- Propose the minimal patch.
- Avoid unnecessary refactoring.

5. Patch Application
- Modify only necessary files.
- Preserve public APIs.

6. Validation
- Re-run tests.
- Report whether the patch passes.

Output a structured report with:
- bug_detected
- bug_type
- location
- evidence
- repair_summary
- validation_result
- remaining_risks
```

这个方案非常适合“轻微改代码”。  
但如果只做 skill，实验对比和自动评估可能还不够强，所以建议再补几个小脚本。

---

# 五、推荐你做的最终方案：Skill + 专用评估脚本

你可以保留 OpenNova 的主体，只新增一个 `repair_bench` 或 `experiments` 目录。

建议目录结构：

```text
OpenNova/
  .opennova/
    skills/
      code_repair/
        SKILL.md

  repair_bench/
    datasets/
      bug_001/
        buggy.py
        test_buggy.py
        metadata.json
      bug_002/
        buggy.py
        test_buggy.py
        metadata.json

    scripts/
      run_pure_llm.py
      run_opennova_repair.py
      evaluate.py

    reports/
      results.json
      results.md

  docs/
    code_repair_skill_usage.md
    experiment_protocol.md
```

这样做有几个好处：

1. 不破坏 OpenNova 原本代码；
2. 很容易解释“我在通用 Agent 上扩展了一个任务专用能力”；
3. 可复现；
4. 容易做实验；
5. 能满足老师关于 plugin/skills 使用说明的要求。

---

# 六、你的“智能模型驱动方法”具体怎么落在 OpenNova 上？

你可以把 OpenNova 的现有能力映射成方法模块。

| 智能模型驱动模块 | OpenNova 中对应能力 |
|---|---|
| LLM 推理模型 | OpenAI / Anthropic / DeepSeek provider |
| Agent 循环推理 | ReAct runtime |
| 工具调用 | read_file、execute_command、write_file、git_diff 等 |
| 短期记忆 | conversation history / working memory |
| 长上下文管理 | context compression |
| 测试反馈模型 | execute_command 运行 pytest |
| 修复执行 | write_file / diff patch pipeline |
| 安全控制 | guardrails |
| 技能封装 | code_repair skill |
| 可复现评估 | repair_bench scripts |

你可以在报告里强调：

> The proposed method is not a single prompt. It is a multi-stage agentic workflow implemented on top of OpenNova.

---

# 七、纯大模型工具方法怎么做？

为了对比公平，你需要实现一个 baseline。

这个 baseline 不使用 OpenNova 的工具循环、不读多轮、不运行测试，只是：

```text
把 buggy.py 内容放进 prompt
直接要求 LLM 判断 bug 和修复建议
```

例如：

```text
Please analyze the following Python code.
Detect whether it contains a bug.
If yes, classify the bug type and suggest a repair.
Return JSON only.
```

这就是“纯大模型工具方法”。

你可以写成：

```text
repair_bench/scripts/run_pure_llm.py
```

它读取每个 `buggy.py`，调用同一个模型，输出 JSON。

---

# 八、OpenNova 智能模型驱动方法怎么跑？

你可以有两种方式。

## 方式 1：命令行调用 OpenNova Skill

例如：

```bash
uv run opennova run "/skill code_repair repair_bench/datasets/bug_001"
```

或者：

```bash
uv run opennova run "Use the code_repair skill to analyze repair_bench/datasets/bug_001"
```

这个很符合“Code Assistant 使用说明”。

## 方式 2：写实验脚本批量调用 OpenNova

如果 OpenNova 的 CLI 便于调用，你可以写：

```bash
python repair_bench/scripts/run_opennova_repair.py
```

脚本内部对每个样本执行 OpenNova 命令，并保存输出。

这更适合实验评估。

---

# 九、你至少需要增加哪些内容？

我建议你最低限度加这 6 类内容。

## 1. `code_repair` skill

用于证明你把通用 agent 封装成了代码修复 assistant。

## 2. 小型 bug 数据集

比如 20 到 40 个样本就够了。

每个样本包含：

```text
buggy.py
test_buggy.py
metadata.json
```

## 3. 纯 LLM baseline 脚本

用于比较。

## 4. OpenNova repair 方法脚本

用于运行你的方法。

## 5. 评估脚本

计算：

- bug detection accuracy；
- bug type accuracy；
- repair success rate；
- JSON validity；
- average runtime；
- token 或 API 调用次数，如果方便的话。

## 6. 文档

包括：

- 如何安装；
- 如何配置 API；
- 如何运行 baseline；
- 如何运行 OpenNova repair；
- 如何运行测试；
- 如何复现实验；
- skill 使用说明。

---

# 十、报告里可以怎么命名两种方法？

你可以这样命名：

## Baseline

> **Pure LLM Tool Method**

定义：

> A single-step prompting method that directly asks a general-purpose LLM to detect and repair bugs from raw source code.

## Your Method

> **OpenNova-MDRepair**

或者：

> **Agentic Model-Driven Repair Method**

定义：

> A model-driven code repair workflow that integrates static inspection, tool-based test execution, error pattern classification, structured repair planning, patch generation, and automated validation through an agent runtime.

---

# 十一、实验对比可以突出什么优势？

你不一定要证明你的方法全指标都更强。你可以重点证明：

## 1. 可控性更强

纯 LLM 输出可能格式不稳定；你的方法用 skill 和流程约束输出结构。

## 2. 可验证性更强

纯 LLM 只是“说它修好了”；你的方法真的运行测试。

## 3. 可复现性更强

OpenNova 方法可以保存 session、命令、diff、测试结果。

## 4. 修复成功率更高

因为它能读文件、运行测试、修改代码、再验证。

## 5. 工程可用性更强

它可以嵌入真实代码仓库，而不只是聊天回答。

---

# 十二、这个题目的亮点可以怎么写？

你可以写 3 个亮点：

## 亮点 1：将通用 AI Agent 改造成任务专用模型驱动框架

> We adapt a general-purpose AI coding agent into a task-specific model-driven repair framework.

## 亮点 2：测试反馈驱动的闭环修复

> The framework forms a closed loop of analysis, repair, and validation by executing tests after patch generation.

## 亮点 3：Skill-level 可复现工作流

> The proposed workflow is packaged as a reusable OpenNova skill, making the method reproducible and extensible.

这刚好对应老师要求里的：

> 如果使用 Code Assistant，需要说明 Code Assistant 的使用方式，包括提交 plugin 或 skills 级使用说明。

你这个就非常对题。

---

# 十三、你不建议怎么做？

我不建议你换成完全新的题目，也不建议你做以下事情：

## 不建议 1：重新训练模型

太耗时，也不是课程重点。

## 不建议 2：做太大的自动修复系统

比如支持多语言、多文件复杂项目、自动 PR 修复，这会做崩。

## 不建议 3：只提交 OpenNova 原项目

这样会显得没有围绕课程要求做选题。

## 不建议 4：只写一个 prompt skill

如果只有 prompt，没有数据集、实验和评估，报告会比较虚。

所以最合理的是：

```text
OpenNova 原系统
+ code_repair skill
+ bug benchmark
+ baseline 对比
+ 评估脚本
+ CI 测试
+ 英文报告/PPT
```

---

# 十四、你可以如何向老师解释你的项目？

你可以这样说：

> My project is based on OpenNova, a lightweight AI coding agent that I implemented. Instead of using it as a pure LLM tool, I extend it with a model-driven code repair workflow. The proposed method decomposes code repair into static inspection, test execution, error classification, LLM-guided patch generation, and automated validation. I compare this method with a pure LLM prompting baseline on a small benchmark of Python bugs.

这段非常适合放在 PPT 第 2 或第 3 页。

---

# 十五、最终建议路线

你的最佳路线是：

## 第一步：新建分支

```bash
git checkout -b feature/opennova-mdrepair
```

## 第二步：增加 skill

```text
.opennova/skills/code_repair/SKILL.md
```

## 第三步：增加 benchmark

```text
repair_bench/datasets/
```

做 20 到 40 个 bug 样本。

## 第四步：增加实验脚本

```text
repair_bench/scripts/run_pure_llm.py
repair_bench/scripts/run_opennova_repair.py
repair_bench/scripts/evaluate.py
```

## 第五步：增加文档

```text
docs/code_repair_skill_usage.md
docs/experiment_protocol.md
```

## 第六步：CI 中加入测试

保证：

```bash
uv run pytest
uv run ruff check src/
uv run mypy src/opennova
```

能过。

如果 benchmark 调 API 不适合 CI，就做 mock mode。

---

# 最终结论

**不要换课题。**

你现在的 OpenNova 反而是一个很好的基础。你只需要把它从“通用 AI coding agent”扩展成：

> **面向代码缺陷检测与修复的智能模型驱动 Agent 系统**

最推荐的最终交付形式是：

```text
OpenNova + code_repair skill + bug benchmark + baseline comparison + evaluation scripts + CI tests
```

这样改动不大，但非常符合课程要求，而且比重新做一个系统更有说服力。
