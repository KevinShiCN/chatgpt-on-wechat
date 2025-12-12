# ChatGPT-on-WeChat 项目说明

本文档供 AI 助手参考，了解项目当前状态、工具使用和最佳实践。

## 项目概述

基于大模型的智能聊天机器人，支持微信、企业微信、公众号等多个平台。

**仓库地址**:
- GitHub（主仓库）: https://github.com/KevinShiCN/chatgpt-on-wechat
- Gitee（同步仓库，私有）: https://gitee.com/kevinshicn/chatgpt-on-wechat

**重要说明**：
- 本服务器只与 Gitee 连接（因 GitHub 网络不稳定）
- 其他设备配置双推送：GitHub + Gitee
- Gitee 作为中转/同步仓库

## 重要变更

### 配置同步方案（2025-12）

为解决敏感配置文件（`config.json`）在多服务器间同步的问题，已部署私有仓库+脚本方案：

- **私有配置仓库**:
  - GitHub（主仓库）: https://github.com/KevinShiCN/chatgpt-configs（私有）
  - Gitee（同步仓库，私有）: https://gitee.com/kevinshicn/chatgpt-configs（私有）
- **同步工具**: `sync-config.sh`
- **支持平台**: Linux / macOS / WSL

**说明**：
- 本服务器配置仓库只连接 Gitee
- 其他设备同样配置双推送：GitHub + Gitee

**为什么需要这个方案？**
- `config.json` 包含 API 密钥等敏感信息
- 已在 `.gitignore` 中排除，不会提交到公开仓库
- 需要在多台服务器间安全同步配置
- 保持配置的版本控制和历史记录

## 配置同步工具使用

### 日常操作

```bash
# 修改配置后推送到私有仓库
./sync-config.sh push

# 从私有仓库拉取最新配置
./sync-config.sh pull

# 查看本地与私有仓库的配置差异
./sync-config.sh status

# 查看帮助
./sync-config.sh help
```

### 新服务器初始化

```bash
# 1. 克隆项目代码
git clone https://github.com/KevinShiCN/chatgpt-on-wechat.git
cd chatgpt-on-wechat

# 2. 初始化配置同步（会自动克隆私有配置仓库）
./sync-config.sh init

# 3. 拉取配置文件
./sync-config.sh pull

# 4. 启动项目
python3 app.py
```

### 环境变量配置（可选）

```bash
# 自定义配置仓库路径
export CONFIG_REPO_DIR="$HOME/my-custom-configs"

# 自定义配置仓库地址
export CONFIG_REPO_URL="https://github.com/USERNAME/custom-configs.git"
```

## 项目结构

```
chatgpt-on-wechat-master/
├── app.py                      # 主程序入口
├── config.json                 # 配置文件（敏感，不在git中）
├── config-template.json        # 配置模板
├── sync-config.sh              # 配置同步脚本
├── bot/                        # 机器人核心
├── channel/                    # 渠道适配（微信、企业微信等）
├── plugins/                    # 插件系统
└── ...

~/chatgpt-configs/              # 私有配置仓库（独立）
├── config.json                 # 配置备份
└── README.md
```

## Git 配置

### 远程仓库

**本服务器配置**（只连接 Gitee）:
- fetch: https://gitee.com/kevinshicn/chatgpt-on-wechat
- push: https://gitee.com/kevinshicn/chatgpt-on-wechat

**其他设备配置**（双推送）:
- fetch: https://github.com/KevinShiCN/chatgpt-on-wechat（从 GitHub 拉取）
- push: https://github.com/KevinShiCN/chatgpt-on-wechat + https://gitee.com/kevinshicn/chatgpt-on-wechat（同时推送）

**架构说明**:
```
GitHub（主库）← 其他设备推送
     ↓
  Gitee（同步库）← 本服务器只连接这里
```

**配置仓库同理**:
- GitHub: https://github.com/KevinShiCN/chatgpt-configs（私有）
- Gitee: https://gitee.com/kevinshicn/chatgpt-configs（私有）

📖 **其他设备配置指南**: 参见 [SETUP_OTHER_DEVICES.md](./SETUP_OTHER_DEVICES.md)

### 分支策略

- `master`: 主分支，稳定版本

### 提交规范

遵循 Conventional Commits：

```
<type>(<scope>): <subject>

<body>
```

**常用类型**:
- `feat`: 新功能
- `fix`: 修复
- `docs`: 文档
- `refactor`: 重构
- `chore`: 构建/配置
- `test`: 测试

**示例**:
```
feat(tools): 添加配置文件同步脚本

添加跨平台配置同步工具，用于在私有仓库和本地项目间安全同步敏感配置文件。
```

## 敏感文件管理

### 已排除的文件（不提交到公开仓库）

在 `.gitignore` 中已配置：

```
config.json          # 主配置
plugins.json         # 插件配置
config.yaml
client_config.json
*.pkl                # 会话数据
*.log                # 日志文件
nohup.out
```

### 配置文件位置

- **本地**: `/www/wwwroot/chatgpt-on-wechat-master/config.json`
- **私有仓库**: `~/chatgpt-configs/config.json`

## 备份说明

项目完整备份位于：`/www/wwwroot/chatgpt-on-wechat-master.backup`

## AI 助手协作指南

### 当需要修改配置时

1. ✅ 可以直接修改 `config.json`
2. ✅ 提醒用户运行 `./sync-config.sh push` 推送配置
3. ❌ 不要将 `config.json` 添加到 git

### 当添加新的敏感文件时

1. 检查是否已在 `.gitignore` 中
2. 如需同步到其他服务器，添加到 `sync-config.sh` 的 `CONFIG_FILES` 数组

### 当用户询问配置同步问题时

1. 优先推荐使用 `sync-config.sh` 脚本
2. 说明这是跨平台方案（Linux/macOS/WSL）
3. 强调私有仓库的安全性

## 常见问题

### Q: Windows 如何使用配置同步？

**A**: 安装 WSL（Windows Subsystem for Linux），在 WSL 中使用脚本。或者使用 Git Bash（可能需要调整）。

### Q: 如何在多台服务器间同步配置？

**A**: 在服务器 A 修改配置后运行 `./sync-config.sh push`，在服务器 B 运行 `./sync-config.sh pull`。

### Q: GitHub 网络不稳定怎么办？

**本服务器**：
- 已配置只连接 Gitee，不受 GitHub 网络影响
- 直接使用 `git push` 和 `git pull`

**其他设备**（Windows/Mac）：
- 配置双推送：同时推送到 GitHub 和 Gitee
- 拉取时如果 GitHub 失败，可从 Gitee 拉取：
  ```bash
  git remote add gitee https://gitee.com/kevinshicn/chatgpt-on-wechat.git
  git pull gitee master
  ```

**详细配置**: 参见 [SETUP_OTHER_DEVICES.md](./SETUP_OTHER_DEVICES.md)

### Q: 配置仓库密钥在哪里？

**A**: Token 已配置在仓库的 remote URL 中。如需重新配置，参考 Git 仓库设置文档。

### Q: 如何添加新的配置文件到同步列表？

**A**: 编辑 `sync-config.sh`，在 `CONFIG_FILES` 数组中添加文件名：

```bash
CONFIG_FILES=(
    "config.json"
    "plugins.json"
    "your-new-file.json"  # 添加新文件
)
```

## 更新日志

### 2025-12-13
- ✅ 添加配置同步脚本 `sync-config.sh`
- ✅ 创建私有配置仓库（GitHub + Gitee）
- ✅ 完成多服务器配置同步测试
- ✅ 添加 CLAUDE.md 项目文档
- ✅ 配置 Gitee 同步仓库（代码 + 配置，全部私有）
- ✅ 本服务器调整为只连接 Gitee（GitHub 网络不稳定）
- ✅ 创建其他设备配置指南 SETUP_OTHER_DEVICES.md
- ✅ 实现 GitHub（主库）+ Gitee（同步库）架构

### 2025-12-12
- ✅ Git 仓库初始化
- ✅ 关联远程仓库
- ✅ 完整备份验证

## 参考资源

- [项目 README](./README.md)
- [配置模板](./config-template.json)
- [插件文档](./plugins/README.md)
