"""Project guide management for persistent project onboarding context."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

GUIDE_FILENAME = "OPENNOVA.md"
DEFAULT_CONTEXT_MAX_CHARS = 5000


@dataclass
class InitResult:
    """Result for project guide initialization."""

    status: str
    path: Path
    message: str
    overwritten: bool = False


class ProjectGuideManager:
    """Create and read project-level OPENNOVA.md guides."""

    _ENV_VAR_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
    _MULTI_INTENT_RE = re.compile(r"(并且|并|同时|然后|顺便| and | then )", re.IGNORECASE)
    _INIT_PATTERNS = [
        re.compile(r"^\s*/?init(?:\s+this\s+project)?\s*$", re.IGNORECASE),
        re.compile(r"初始化(这个|当前)?项目", re.IGNORECASE),
        re.compile(r"(初始化|创建|新建).{0,16}(项目|仓库).{0,16}(说明书|文档|指南)", re.IGNORECASE),
        re.compile(r"(初始化|创建|新建).{0,12}OPENNOVA\.md", re.IGNORECASE),
        re.compile(
            r"(initialize|create|bootstrap).{0,24}(project|repo).{0,24}(guide|manual|spec|onboarding|opennova\.md)",
            re.IGNORECASE,
        ),
        re.compile(r"(initialize|create|bootstrap).{0,24}opennova\.md", re.IGNORECASE),
        re.compile(r"open\s?nova.{0,20}project.{0,20}(init|initialize)", re.IGNORECASE),
    ]

    def __init__(self, project_path: str | Path = "."):
        self.project_path = Path(project_path).resolve()
        self.guide_path = self.project_path / GUIDE_FILENAME

    def exists(self) -> bool:
        """Return whether OPENNOVA.md already exists."""
        return self.guide_path.exists()

    def create_or_skip(self, force: bool = False, content: str | None = None) -> InitResult:
        """Create OPENNOVA.md unless it already exists."""
        if self.exists() and not force:
            return InitResult(
                status="skipped",
                path=self.guide_path,
                message=(
                    f"{GUIDE_FILENAME} already exists at {self.guide_path}. "
                    "Use /init --force to regenerate."
                ),
            )

        if content is None:
            content = self._render_guide()
        overwritten = self.exists()
        self.guide_path.write_text(content, encoding="utf-8")

        if overwritten:
            return InitResult(
                status="overwritten",
                path=self.guide_path,
                message=f"Regenerated {GUIDE_FILENAME} at {self.guide_path}",
                overwritten=True,
            )
        return InitResult(
            status="created",
            path=self.guide_path,
            message=f"Created {GUIDE_FILENAME} at {self.guide_path}",
            overwritten=False,
        )

    def load_for_context(self, max_chars: int = DEFAULT_CONTEXT_MAX_CHARS) -> str | None:
        """Load the project guide text for context injection."""
        if not self.exists():
            return None

        text = self.guide_path.read_text(encoding="utf-8").strip()
        if not text:
            return None

        if len(text) <= max_chars:
            return text

        return (
            text[:max_chars].rstrip()
            + "\n\n[... OPENNOVA.md content truncated for context budget ...]"
        )

    def is_high_confidence_init_request(self, task: str) -> bool:
        """Detect whether task text is a high-confidence project guide init request."""
        normalized = (task or "").strip()
        if not normalized:
            return False
        if len(normalized) > 180:
            return False
        if self._MULTI_INTENT_RE.search(normalized):
            return False

        return any(pattern.search(normalized) for pattern in self._INIT_PATTERNS)

    def build_generation_brief(self) -> str:
        """Build a concise, factual project brief for LLM guide generation."""
        pyproject = self._load_pyproject()
        project_name = self._project_name(pyproject)
        description = self._project_description(pyproject)
        stacks = self._detect_tech_stack(pyproject)
        root_items = self._top_level_items()
        env_vars = self._detect_env_vars()
        deps = self._third_party_services(pyproject)
        readme_excerpt = self._readme_excerpt(max_chars=2200)

        stack_listing = "\n".join(f"- {item}" for item in stacks) or "- （待补充）"
        root_listing = "\n".join(f"- `{item}`" for item in root_items) or "- （待补充）"
        env_listing = "\n".join(f"- `{name}`" for name in env_vars) or "- （暂无检测到）"
        dep_listing = "\n".join(f"- {item}" for item in deps) or "- （待补充）"

        return f"""Project root: {self.project_path}
Project name: {project_name}
Project description: {description}

Detected tech stack:
{stack_listing}

Top-level repo entries:
{root_listing}

Detected env vars:
{env_listing}

Detected third-party services:
{dep_listing}

README excerpt:
{readme_excerpt}
"""

    @staticmethod
    def normalize_generated_markdown(content: str) -> str:
        """Normalize LLM output into a clean markdown document."""
        text = (content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    def _render_guide(self) -> str:
        """Render an initialized OPENNOVA.md file."""
        pyproject = self._load_pyproject()
        project_name = self._project_name(pyproject)
        description = self._project_description(pyproject)
        stacks = self._detect_tech_stack(pyproject)
        root_items = self._top_level_items()
        env_vars = self._detect_env_vars()
        deps = self._third_party_services(pyproject)
        line_length = self._line_length(pyproject)
        now = datetime.now().isoformat(timespec="seconds")

        root_listing = "\n".join(f"- `{item}`" for item in root_items) or "- （待补充）"
        stack_listing = "\n".join(f"- {item}" for item in stacks)
        env_listing = "\n".join(f"- `{name}`" for name in env_vars) or "- （暂无检测到，后续补充）"
        dep_listing = "\n".join(f"- {item}" for item in deps) or "- （待补充）"

        return f"""# {project_name} 项目说明书（OPENNOVA）

> 本文件由 `/init` 于 {now} 初始化生成。可以手动编辑；OpenNova 在任务中会自动参考此文件。

## 项目概述
- 项目名称：`{project_name}`
- 项目简介：{description}
- 目标：帮助 AI 助手快速理解代码库、开发习惯和注意事项。

## 技术栈
{stack_listing}

## 目录结构
以下为仓库根目录的主要条目（自动扫描）：
{root_listing}

## 常用命令
- `uv sync`
- `uv run opennova`
- `uv run opennova tui`
- `uv run pytest`
- `uv run ruff check src/`
- `uv run ruff format src/`

## 编码规范
- Python 版本：`3.11+`
- 建议行宽：`{line_length}`
- 默认要求：补充类型标注，优先小步提交并保持可读性。

## 架构约定
- Runtime 负责 Agent 生命周期、工具注册、会话和上下文。
- Tools 目录存放可执行能力，变更应附带测试。
- Skills 目录存放 `SKILL.md` 能力包，用于高层行为编排。

## 测试要求
- 提交前至少运行：`uv run pytest`
- 涉及核心逻辑改动时建议补充单元测试。
- 建议在本地通过 lint / format 后再提交。

## 环境变量说明
{env_listing}

## 第三方服务
{dep_listing}

## 已知问题
- （待补充）记录当前已知缺陷、复现条件和临时规避方案。

## 禁止事项
- 禁止未经确认执行破坏性命令（如 `rm -rf`、强制重置历史）。
- 禁止绕过测试直接合并高风险改动。
- 禁止在不说明原因的情况下覆盖用户手动维护内容。

## 工作流偏好
- 优先读取上下文后再修改代码，尽量小步快跑。
- 关键变更需明确验证步骤与结果。
- 当需求存在歧义时先澄清高风险决策点。
"""

    def _load_pyproject(self) -> dict[str, Any]:
        pyproject_path = self.project_path / "pyproject.toml"
        if not pyproject_path.exists():
            return {}
        try:
            return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _project_name(self, pyproject: dict[str, Any]) -> str:
        project = pyproject.get("project", {})
        return project.get("name") or self.project_path.name

    def _project_description(self, pyproject: dict[str, Any]) -> str:
        project = pyproject.get("project", {})
        return project.get("description") or "（待补充）"

    def _line_length(self, pyproject: dict[str, Any]) -> str:
        tool = pyproject.get("tool", {})
        ruff_line_length = tool.get("ruff", {}).get("line-length")
        black_line_length = tool.get("black", {}).get("line-length")
        value = ruff_line_length or black_line_length
        return str(value) if value else "100"

    def _detect_tech_stack(self, pyproject: dict[str, Any]) -> list[str]:
        project = pyproject.get("project", {})
        deps = [str(dep).lower() for dep in project.get("dependencies", [])]
        dev_deps = [str(dep).lower() for dep in project.get("optional-dependencies", {}).get("dev", [])]

        stack = ["Python 3.11+"]
        if any("openai" in dep for dep in deps):
            stack.append("OpenAI API")
        if any("anthropic" in dep for dep in deps):
            stack.append("Anthropic API")
        if any("deepseek" in dep for dep in deps):
            stack.append("DeepSeek API")
        if any("textual" in dep for dep in deps):
            stack.append("Textual TUI")
        if any("pytest" in dep for dep in dev_deps):
            stack.append("pytest")
        if any("ruff" in dep for dep in dev_deps):
            stack.append("ruff")
        if any("mypy" in dep for dep in dev_deps):
            stack.append("mypy")
        return stack

    def _top_level_items(self) -> list[str]:
        ignored = {".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
        items = []
        for path in sorted(self.project_path.iterdir(), key=lambda p: p.name.lower()):
            if path.name in ignored:
                continue
            if path.name.startswith(".") and path.name != ".opennova":
                continue
            items.append(path.name + ("/" if path.is_dir() else ""))
        return items[:20]

    def _detect_env_vars(self) -> list[str]:
        candidates: set[str] = set()
        for file_name in ("README.md", "README.zh-CN.md", ".env.example", "config.example.yaml"):
            path = self.project_path / file_name
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for match in self._ENV_VAR_RE.findall(content):
                if match.startswith("OPENAI_") or match.startswith("ANTHROPIC_") or match.startswith("DEEPSEEK_"):
                    candidates.add(match)

        default_vars = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"}
        return sorted(candidates | default_vars)

    def _third_party_services(self, pyproject: dict[str, Any]) -> list[str]:
        project = pyproject.get("project", {})
        deps = [str(dep).lower() for dep in project.get("dependencies", [])]
        services = []
        if any("openai" in dep for dep in deps):
            services.append("OpenAI")
        if any("anthropic" in dep for dep in deps):
            services.append("Anthropic")
        if any("deepseek" in dep for dep in deps):
            services.append("DeepSeek")
        return services

    def _readme_excerpt(self, max_chars: int = 2200) -> str:
        for file_name in ("README.md", "README.zh-CN.md"):
            path = self.project_path / file_name
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            if len(text) <= max_chars:
                return text
            return text[:max_chars].rstrip() + "\n..."
        return "（README not found）"
