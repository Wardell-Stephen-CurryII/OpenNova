# OpenNova 快速开始指南

5 分钟快速上手 OpenNova。

## 第一步：安装

```bash
# 克隆仓库
git clone https://github.com/Wardell-Stephen-CurryII/OpenNova.git
cd OpenNova

# 安装依赖
uv sync

# 初始化配置
uv run opennova init
```

## 第二步：配置 API Key

选择一种方式：

**方式 A - 环境变量（推荐）：**
```bash
export OPENAI_API_KEY="sk-your-key"
```

**方式 B - 配置文件：**
```bash
nano ~/.opennova/config.yaml
# 将 api_key: "${OPENAI_API_KEY}" 改为你的实际 key
```

## 第三步：开始使用

```bash
# 启动交互模式
uv run opennova

# 或直接执行任务
uv run opennova run "读取 README.md"
```

## 示例任务

```
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
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `uv run opennova` | 启动 REPL |
| `uv run opennova run "task"` | 执行单次任务 |
| `uv run opennova --version` | 查看版本 |
| `/help` | REPL 内帮助 |
| `/exit` | 退出 REPL |

## 获取帮助

- 📖 [完整教程](docs/TUTORIAL.md)
- 🐛 [报告问题](https://github.com/Wardell-Stephen-CurryII/OpenNova/issues)
- 💬 [GitHub Discussions](https://github.com/Wardell-Stephen-CurryII/OpenNova/discussions)
