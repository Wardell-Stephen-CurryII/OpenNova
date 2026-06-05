# OpenNova 快速开始指南

5 分钟快速上手当前版本的 OpenNova。

> 本文档以仓库开发者视角为主，默认使用 `uv run opennova ...`。如果你已经通过 `uv tool install .` 安装了全局 CLI，可以把命令里的 `uv run` 去掉。

## 第一步：安装

```bash
# 克隆仓库
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# 安装依赖
uv sync

# 初始化全局配置（生成 ~/.opennova/config.yaml）
uv run opennova init
```

## 第二步：配置模型

当前默认配置：

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro
```

推荐直接使用环境变量提供 API Key：

```bash
export DEEPSEEK_API_KEY="sk-your-deepseek-key"
```

如果你想改成其他 provider，也可以编辑 `~/.opennova/config.yaml`：

```yaml
default_provider: deepseek
default_model: deepseek-v4-pro

providers:
  openai:
    api_key: ${OPENAI_API_KEY}
    default_model: gpt-4o

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    default_model: claude-sonnet-4

  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com/v1
    default_model: deepseek-v4-pro
```

## 第三步：开始使用

```bash
# 启动交互模式（默认进入 TUI）
uv run opennova

# 使用经典 REPL
uv run opennova run --no-tui

# 直接执行单个任务
uv run opennova run "读取 README.md"
```

## 第四步：初始化项目记忆

进入项目后，可以让 OpenNova 为当前仓库生成一份长期项目说明：

```text
/init
```

它会在项目根目录创建 `OPENNOVA.md`。这个文件会被模型自动读取，用来帮助后续任务更快理解代码库、目录结构、工作流和注意事项。

如果文件已经存在但你想重建：

```text
/init --force
```

## 示例任务

```text
# 文件操作
opennova> 读取 src/main.py
opennova> 创建 test.py 文件
opennova> 列出目录结构

# 代码生成
opennova> 写一个计算斐波那契数列的函数

# Shell 命令
opennova> 运行 pytest tests/

# 计划模式
opennova> /plan 重构认证模块

# 项目初始化
opennova> /init
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `uv run opennova` | 启动交互模式 |
| `uv run opennova run "task"` | 执行单次任务 |
| `uv run opennova run --no-tui` | 使用经典 REPL |
| `uv run opennova --version` | 查看版本 |
| `uv run opennova init` | 初始化全局配置 |
| `/init [--force]` | 生成或重建 `OPENNOVA.md` |
| `/help` | 查看交互命令帮助 |
| `/exit` | 退出当前会话 |

## 获取帮助

- [完整教程](TUTORIAL.md)
- [API 文档](API.md)
- [报告问题](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
- [GitHub Discussions](https://github.com/Wardell-Stephen-CurryII/OpenNova/discussions)
