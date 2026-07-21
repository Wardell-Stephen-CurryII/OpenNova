# OpenNova 0.4.2 快速开始

OpenNova 的交互界面是 Textual TUI。旧的交互式 CLI 和独立 `opennova tui` 命令已移除；直接运行 `opennova` 即可进入工作台。

## 1. 安装

```bash
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova
uv sync
uv run opennova init
```

`init` 会创建 `~/.opennova/config.yaml`。也可以用 `uv tool install .` 安装全局命令，之后省略示例中的 `uv run`。

## 2. 配置模型

推荐通过环境变量提供密钥：

```bash
export DEEPSEEK_API_KEY="sk-your-key"
# 或 OPENAI_API_KEY / ANTHROPIC_API_KEY
```

默认 provider 和模型可以在全局或项目配置中修改：

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
    default_model: deepseek-v4-pro
```

项目级 `.opennova/config.yaml` 会覆盖全局配置。

## 3. 启动

```bash
# 打开 Textual TUI
uv run opennova

# 选择一个历史会话
uv run opennova --resume

# 继续最近会话
uv run opennova --continue

# 单次非交互任务
uv run opennova run "读取 README.md"
```

`--resume` 会显示按最近修改时间排序的会话选择器；恢复后消息区和后台上下文都会还原，并继续使用原 session。

## 4. 第一次对话

在 TUI 输入：

```text
分析当前项目结构，并告诉我应该先阅读哪些文件
```

常用工作流：

```text
/init
/plan 为用户模块增加测试
/tools
/todos
/status
```

`/init` 会在项目根目录生成 `OPENNOVA.md`，后续任务会自动读取它作为项目说明。

## 5. 复制消息文字

直接用鼠标在消息区拖选文字，然后使用 `Ctrl+Shift+C`；macOS 终端能传递快捷键时也可使用 `Cmd+C`。`Ctrl+C` 保留为取消当前 Agent 任务。

## 常用入口

| 命令 | 说明 |
|---|---|
| `uv run opennova` | 打开 TUI |
| `uv run opennova --resume` | 打开会话选择器 |
| `uv run opennova --continue` | 继续最近会话 |
| `uv run opennova --permission-mode request\|auto\|full` | 选择审批模式 |
| `uv run opennova run "task"` | 执行单次非交互任务 |
| `uv run opennova init` | 创建全局配置 |
| `uv run opennova list-tools` | 查看当前工具 |
| `uv run opennova config` | 查看合并后的配置 |
| `uv run opennova doctor` | 无副作用检查运行环境 |
| `uv run opennova --version` | 查看版本 |

## 常用 TUI 命令

| 命令 | 说明 |
|---|---|
| `/act <task>` | 直接执行 |
| `/plan <task>` | 生成计划并确认执行 |
| `/resume [id]` | 选择或恢复会话 |
| `/fork [id]` | 分叉当前或指定会话 |
| `/permissions ...` | 查看或修改审批规则 |
| `/plugins ...` | 管理项目插件 |
| `/hooks [trust|untrust]` | 查看或管理项目 hooks 信任 |
| `/automations ...` | 管理本地自动化 |
| `/diagnostics [path]` | 运行 Python 诊断 |
| `/checkpoint ...` | 查看或恢复文件检查点 |
| `/memory ...` | 管理分层项目记忆 |
| `/export [dir]` | 导出 Markdown 对话记录 |
| `/help` | 查看完整命令列表 |
| `/exit` | 退出 |

路径包含中文时，测试建议使用：

```bash
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 PYTHONUTF8=1 uv run pytest
```

继续阅读：[完整教程](TUTORIAL.md) · [API 文档](API.md) · [问题反馈](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
