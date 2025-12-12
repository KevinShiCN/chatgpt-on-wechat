# 其他设备 Git 配置指南

本指南用于在其他设备（Windows/Mac/其他Linux服务器）上配置双仓库同步。

## 📐 架构说明

```
┌─────────────────┐
│  GitHub (主库)   │ ← 公开/主要仓库
└────────┬────────┘
         │
         ↓ (其他设备双推送)
┌─────────────────┐
│ Gitee (同步库)   │ ← 私有/同步仓库 ← 本服务器只连接这里
└─────────────────┘
```

**工作流程**：
- **本服务器**：只与 Gitee 交互（因 GitHub 网络不稳定）
- **其他设备**：同时推送到 GitHub 和 Gitee
- **Gitee 作用**：中转站/同步仓库，确保所有设备都能访问最新代码

---

## 🔧 其他设备配置步骤

### 方式一：新克隆项目

```bash
# 1. 从 GitHub 克隆项目（推荐从主库克隆）
git clone https://github.com/KevinShiCN/chatgpt-on-wechat.git
cd chatgpt-on-wechat

# 2. 配置双推送（GitHub + Gitee）
git remote set-url --add --push origin https://ghp_YOUR_GITHUB_TOKEN@github.com/KevinShiCN/chatgpt-on-wechat.git
git remote set-url --add --push origin https://kevinshicn:e34228e375c12cd5e51543e7cbc5d1ea@gitee.com/kevinshicn/chatgpt-on-wechat.git

# 3. 验证配置
git remote -v
# 应该显示：
# origin  https://github.com/... (fetch)
# origin  https://github.com/... (push)
# origin  https://gitee.com/... (push)
```

### 方式二：已有项目添加 Gitee

```bash
# 进入项目目录
cd chatgpt-on-wechat

# 添加 Gitee 推送地址
git remote set-url --add --push origin https://kevinshicn:e34228e375c12cd5e51543e7cbc5d1ea@gitee.com/kevinshicn/chatgpt-on-wechat.git

# 验证配置
git remote -v
```

---

## 📝 配置仓库同样需要双推送

### 代码仓库

```bash
# 位置：~/chatgpt-configs/
cd ~/chatgpt-configs

# 配置双推送
git remote set-url origin https://ghp_YOUR_GITHUB_TOKEN@github.com/KevinShiCN/chatgpt-configs.git
git remote set-url --add --push origin https://ghp_YOUR_GITHUB_TOKEN@github.com/KevinShiCN/chatgpt-configs.git
git remote set-url --add --push origin https://kevinshicn:e34228e375c12cd5e51543e7cbc5d1ea@gitee.com/kevinshicn/chatgpt-configs.git

# 验证
git remote -v
```

---

## 🔑 令牌说明

### GitHub Token
- 获取地址：https://github.com/settings/tokens
- 权限：勾选 `repo`
- 格式：`ghp_xxxxxxxxxxxxxx`（40个字符）

### Gitee Token（已提供）
- Token：`e34228e375c12cd5e51543e7cbc5d1ea`
- 用户名：`kevinshicn`

---

## ✅ 使用验证

### 推送测试

```bash
# 修改文件
echo "test" >> README.md

# 提交
git add README.md
git commit -m "test: 测试双推送"

# 推送（会自动推送到 GitHub 和 Gitee）
git push
```

**预期结果**：
```
To https://github.com/KevinShiCN/chatgpt-on-wechat.git
   xxx..yyy  master -> master
To https://gitee.com/kevinshicn/chatgpt-on-wechat.git
   xxx..yyy  master -> master
```

### 拉取测试

```bash
# 从 GitHub 拉取（默认）
git pull

# 或从 Gitee 拉取（网络问题时）
git remote add gitee https://gitee.com/kevinshicn/chatgpt-on-wechat.git
git pull gitee master
```

---

## 🎯 日常工作流

### 在其他设备开发

```bash
# 1. 拉取最新代码
git pull

# 2. 进行修改
# ...

# 3. 提交并推送（自动同步到两个平台）
git add .
git commit -m "feat: 新功能"
git push  # 自动推送到 GitHub 和 Gitee
```

### 在本服务器同步

```bash
# 从 Gitee 拉取（本服务器只连接 Gitee）
git pull

# 修改配置
vim config.json

# 推送配置（通过 sync-config.sh）
./sync-config.sh push  # 推送到 Gitee

# 推送代码（如果有代码修改）
git push  # 推送到 Gitee
```

---

## 📊 同步场景示例

### 场景1：在 Windows 电脑开发新功能

```bash
# Windows 电脑
git pull
# 开发新功能...
git commit -m "feat: 新功能"
git push  # → 推送到 GitHub + Gitee

# 本服务器同步
git pull  # ← 从 Gitee 拉取
```

### 场景2：在服务器修改配置

```bash
# 本服务器
vim config.json
./sync-config.sh push  # → 推送配置到 Gitee

# Windows 电脑同步配置
./sync-config.sh pull  # ← 从 Gitee 拉取
```

---

## ⚠️ 重要提醒

### 1. Token 安全
- GitHub 和 Gitee Token 都是敏感信息
- 不要提交到代码仓库
- 定期更换 Token

### 2. 推送顺序
- 配置中 GitHub 在前，Gitee 在后
- 如果 GitHub 推送失败，Gitee 仍会尝试推送
- 建议：确保 GitHub 推送成功（GitHub 是主仓库）

### 3. 冲突处理
- 如果两个平台有冲突，优先以 GitHub 为准
- 解决冲突后，再推送到两个平台

---

## 🔧 故障排查

### GitHub 推送失败

```bash
# 检查 Token 是否过期
curl -H "Authorization: token ghp_YOUR_TOKEN" https://api.github.com/user

# 重新配置 Token
git remote set-url origin https://ghp_NEW_TOKEN@github.com/KevinShiCN/chatgpt-on-wechat.git
```

### Gitee 推送失败

```bash
# 检查 Token
curl -s "https://gitee.com/api/v5/user?access_token=e34228e375c12cd5e51543e7cbc5d1ea"

# 重新配置
git remote set-url --add --push origin https://kevinshicn:NEW_TOKEN@gitee.com/kevinshicn/chatgpt-on-wechat.git
```

---

## 📖 参考命令

```bash
# 查看远程配置
git remote -v

# 查看所有推送地址
git config --get-all remote.origin.pushurl

# 删除所有推送地址
git config --unset-all remote.origin.pushurl

# 重新配置
git remote set-url --add --push origin <URL>
```

---

**更新时间**：2025-12-13
**维护者**：KevinShiCN
