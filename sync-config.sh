#!/bin/bash

# 配置同步脚本 - 跨平台支持 (Linux/macOS/WSL)
# 用途：在私有Git仓库和本地项目间同步敏感配置文件

set -e

# 配置
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_REPO_DIR="${CONFIG_REPO_DIR:-$HOME/chatgpt-configs}"
CONFIG_REPO_URL="${CONFIG_REPO_URL:-https://github.com/KevinShiCN/chatgpt-configs.git}"

# 需要同步的配置文件列表
CONFIG_FILES=(
    "config.json"
    "plugins.json"
)

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印函数
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 检查配置仓库是否存在
check_config_repo() {
    if [ ! -d "$CONFIG_REPO_DIR/.git" ]; then
        return 1
    fi
    return 0
}

# 初始化配置仓库
init_config_repo() {
    info "初始化配置仓库..."

    if check_config_repo; then
        warn "配置仓库已存在: $CONFIG_REPO_DIR"
        return 0
    fi

    # 检查远程仓库是否存在
    if git ls-remote "$CONFIG_REPO_URL" &>/dev/null; then
        info "克隆现有配置仓库..."
        git clone "$CONFIG_REPO_URL" "$CONFIG_REPO_DIR"
    else
        info "创建新的配置仓库..."
        mkdir -p "$CONFIG_REPO_DIR"
        cd "$CONFIG_REPO_DIR"
        git init

        # 创建 README
        cat > README.md << 'EOF'
# ChatGPT 配置文件私有仓库

此仓库存储 chatgpt-on-wechat 项目的敏感配置文件。

**⚠️ 警告：此仓库包含敏感信息，请确保为私有仓库！**

## 配置文件列表
- config.json - 主配置文件
- plugins.json - 插件配置

## 使用方法
使用项目中的 sync-config.sh 脚本自动同步。
EOF

        git add README.md
        git commit -m "Initial commit"
        git branch -M main
        git remote add origin "$CONFIG_REPO_URL"

        info "请在 GitHub 创建私有仓库: $CONFIG_REPO_URL"
        info "然后运行: cd $CONFIG_REPO_DIR && git push -u origin main"
    fi

    # 复制当前配置文件到配置仓库
    cd "$PROJECT_DIR"
    for file in "${CONFIG_FILES[@]}"; do
        if [ -f "$file" ]; then
            info "复制 $file 到配置仓库..."
            cp "$file" "$CONFIG_REPO_DIR/"
        fi
    done

    info "配置仓库初始化完成: $CONFIG_REPO_DIR"
}

# 推送配置到私有仓库
push_configs() {
    if ! check_config_repo; then
        error "配置仓库不存在，请先运行: $0 init"
    fi

    info "推送配置到私有仓库..."

    cd "$PROJECT_DIR"

    # 复制配置文件到配置仓库
    for file in "${CONFIG_FILES[@]}"; do
        if [ -f "$file" ]; then
            if ! cmp -s "$file" "$CONFIG_REPO_DIR/$file" 2>/dev/null; then
                info "复制 $file..."
                cp "$file" "$CONFIG_REPO_DIR/"
            fi
        else
            warn "文件不存在: $file"
        fi
    done

    # 切换到配置仓库并检查是否有变更
    cd "$CONFIG_REPO_DIR"

    # 只添加存在的文件
    for file in "${CONFIG_FILES[@]}"; do
        if [ -f "$file" ]; then
            git add "$file" 2>/dev/null || true
        fi
    done

    # 检查是否有需要提交的内容
    if git diff --cached --quiet; then
        info "没有配置变更"
        return 0
    fi

    local commit_msg="${1:-Update configs from $(hostname) at $(date +'%Y-%m-%d %H:%M:%S')}"
    git commit -m "$commit_msg"

    info "推送到远程仓库..."
    git push

    info "配置推送完成！"
}

# 从私有仓库拉取配置
pull_configs() {
    if ! check_config_repo; then
        error "配置仓库不存在，请先运行: $0 init"
    fi

    info "从私有仓库拉取配置..."

    # 拉取最新配置
    cd "$CONFIG_REPO_DIR"
    git pull

    # 复制到项目目录
    cd "$PROJECT_DIR"
    for file in "${CONFIG_FILES[@]}"; do
        if [ -f "$CONFIG_REPO_DIR/$file" ]; then
            info "复制 $file 到项目..."
            cp "$CONFIG_REPO_DIR/$file" "$file"
        else
            warn "配置仓库中不存在: $file"
        fi
    done

    info "配置拉取完成！"
}

# 查看配置差异
show_status() {
    if ! check_config_repo; then
        error "配置仓库不存在，请先运行: $0 init"
    fi

    info "检查配置差异..."

    local has_diff=false
    cd "$PROJECT_DIR"

    for file in "${CONFIG_FILES[@]}"; do
        if [ -f "$file" ] && [ -f "$CONFIG_REPO_DIR/$file" ]; then
            if ! cmp -s "$file" "$CONFIG_REPO_DIR/$file"; then
                echo -e "\n${YELLOW}$file 有差异:${NC}"
                diff -u "$CONFIG_REPO_DIR/$file" "$file" || true
                has_diff=true
            fi
        elif [ -f "$file" ]; then
            warn "$file 存在于项目但不在配置仓库"
            has_diff=true
        elif [ -f "$CONFIG_REPO_DIR/$file" ]; then
            warn "$file 存在于配置仓库但不在项目"
            has_diff=true
        fi
    done

    if [ "$has_diff" = false ]; then
        info "配置文件完全同步 ✓"
    fi
}

# 显示帮助信息
show_help() {
    cat << EOF
配置同步脚本 - chatgpt-on-wechat

用法:
    $0 <command> [options]

命令:
    init        初始化配置仓库（首次使用）
    push        推送本地配置到私有仓库
    pull        从私有仓库拉取配置到本地
    status      查看本地和仓库配置的差异
    help        显示此帮助信息

环境变量:
    CONFIG_REPO_DIR     配置仓库本地路径 (默认: ~/chatgpt-configs)
    CONFIG_REPO_URL     配置仓库远程地址 (默认: https://github.com/KevinShiCN/chatgpt-configs.git)

示例:
    # 首次使用，初始化
    $0 init

    # 修改配置后推送
    $0 push

    # 在另一台服务器拉取配置
    $0 pull

    # 查看差异
    $0 status
EOF
}

# 主函数
main() {
    case "${1:-help}" in
        init)
            init_config_repo
            ;;
        push)
            push_configs "$2"
            ;;
        pull)
            pull_configs
            ;;
        status)
            show_status
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            error "未知命令: $1\n运行 '$0 help' 查看帮助"
            ;;
    esac
}

main "$@"
