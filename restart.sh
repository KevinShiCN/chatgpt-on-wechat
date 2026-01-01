#!/bin/bash
# chatgpt-on-wechat 重启脚本
# 用法: ./restart.sh

cd /www/wwwroot/chatgpt-on-wechat-master

echo "=========================================="
echo "  chatgpt-on-wechat 重启脚本"
echo "=========================================="

# 1. 查找并终止现有进程
echo ""
echo "[1/3] 正在查找 app.py 进程..."
PID=$(ps -ef | grep "python.*app.py" | grep -v grep | awk '{print $2}')

if [ -n "$PID" ]; then
    echo "      找到进程 PID: $PID"
    echo "      正在终止进程..."
    kill $PID
    sleep 2

    # 检查是否成功终止
    if ps -p $PID > /dev/null 2>&1; then
        echo "      进程未响应，强制终止..."
        kill -9 $PID
        sleep 1
    fi
    echo "      进程已终止"
else
    echo "      未找到运行中的 app.py 进程"
fi

# 2. 清空旧日志（可选，保留最后1000行）
echo ""
echo "[2/3] 备份日志..."
if [ -f nohup.out ]; then
    tail -1000 nohup.out > nohup.out.bak
    mv nohup.out.bak nohup.out
    echo "      日志已截断（保留最后1000行）"
fi

# 3. 启动新进程
echo ""
echo "[3/3] 正在启动 app.py..."

# 设置 NO_PROXY，让企业微信 API 不走代理
export NO_PROXY="qyapi.weixin.qq.com,api.weixin.qq.com"
export no_proxy="qyapi.weixin.qq.com,api.weixin.qq.com"
echo "      已设置 NO_PROXY: $NO_PROXY"

nohup python3 app.py > nohup.out 2>&1 &
NEW_PID=$!
sleep 2

# 检查是否启动成功
if ps -p $NEW_PID > /dev/null 2>&1; then
    echo "      启动成功！新进程 PID: $NEW_PID"
else
    echo "      启动可能失败，请检查日志"
fi

echo ""
echo "=========================================="
echo "  重启完成，正在显示日志..."
echo "  按 Ctrl+C 退出日志查看"
echo "=========================================="
echo ""

# 4. 显示日志
tail -f nohup.out
