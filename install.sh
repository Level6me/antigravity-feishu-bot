#!/bin/bash

# ==========================================
# Antigravity Feishu Bot 交互式部署脚本
# ==========================================

set -e

# --- 1. 欢迎与环境检测 ---
echo "=========================================="
echo "    Antigravity Feishu Bot 一键部署脚本"
echo "=========================================="
echo ""

if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未检测到 python3，请先安装 Python 3.10+"
    exit 1
fi

if ! command -v pm2 &> /dev/null; then
    echo "❌ 错误: 未检测到 pm2，请先执行 npm install -g pm2"
    exit 1
fi

echo "✅ 环境检测通过 (python3, pm2 已安装)"
echo ""

# --- 2. 交互式环境变量配置 ---
configure_env=true
if [ -f .env ]; then
    read -p "⚠️ 检测到已存在 .env 配置文件，是否覆盖？[y/N]: " overwrite_env
    if [[ ! "$overwrite_env" =~ ^[Yy]$ ]]; then
        configure_env=false
        echo "⏭️ 跳过环境变量配置，使用现有 .env 文件。"
    fi
fi

if [ "$configure_env" = true ]; then
    echo "------------------------------------------"
    echo "请输入飞书应用的配置信息 (可在飞书开发者后台获取):"
    read -p "👉 FEISHU_APP_ID (例: cli_a4...): " app_id
    read -p "👉 FEISHU_APP_SECRET: " app_secret

    if [ -z "$app_id" ] || [ -z "$app_secret" ]; then
        echo "❌ APP_ID 或 APP_SECRET 不能为空，部署中断。"
        exit 1
    fi

    echo "FEISHU_APP_ID=$app_id" > .env
    echo "FEISHU_APP_SECRET=$app_secret" >> .env
    echo "✅ .env 配置文件已成功生成。"
fi
echo ""

# --- 3. 配置虚拟环境与依赖 ---
echo "📦 开始配置 Python 虚拟环境并安装依赖..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 虚拟环境 (venv) 创建成功。"
else
    echo "✅ 虚拟环境 (venv) 已存在。"
fi

# 激活虚拟环境并安装依赖
source venv/bin/activate
pip install --upgrade pip
if [ -f requirements.txt ]; then
    pip install -r requirements.txt
else
    echo "⚠️ 未找到 requirements.txt，尝试直接安装 lark-oapi..."
    pip install lark-oapi
fi
echo "✅ 依赖安装完成。"
echo ""

# --- 4. PM2 启动服务 ---
echo "🚀 准备启动机器人后台服务..."
read -p "是否立即使用 PM2 启动/重启 feishu-bot 服务？[Y/n]: " start_pm2
if [[ ! "$start_pm2" =~ ^[Nn]$ ]]; then
    # 检查进程是否存在
    if pm2 status | grep -q "feishu-bot"; then
        pm2 restart feishu-bot
        echo "✅ 服务已重启。"
    else
        pm2 start venv/bin/python3 --name "feishu-bot" -- main.py
        echo "✅ 服务已启动。"
    fi
    
    echo "💾 正在保存 PM2 进程列表以支持开机自启..."
    pm2 save
    echo ""
    echo "🎉 部署完成！你的飞书机器人现在应该已经上线了。"
    echo "👉 你可以使用 'pm2 logs feishu-bot' 来查看实时运行日志。"
else
    echo "⏭️ 跳过启动。你可以稍后手动运行: pm2 start venv/bin/python3 --name \"feishu-bot\" -- main.py"
fi
