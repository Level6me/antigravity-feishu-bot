#!/bin/bash

# ==========================================
# Antigravity Feishu Bot 交互式部署脚本
# ==========================================

set -e

if [ "$1" == "update" ]; then
    echo "=========================================="
    echo "    Antigravity Feishu Bot 一键升级脚本"
    echo "=========================================="
    echo "⬇️ 正在拉取最新代码..."
    git pull origin main || true
    
    if [ -d "venv" ]; then
        echo "📦 更新依赖..."
        source venv/bin/activate
        pip install -r requirements.txt || true
    fi
    
    echo "🚀 正在重启服务..."
    if command -v pm2 &> /dev/null; then
        pm2 restart feishu-bot || true
        pm2 restart agy-daemon || true
        pm2 save || true
    else
        echo "⚠️ 未检测到 PM2，可能需要您手动重启服务。"
    fi
    echo "✅ 升级并重启完成！"
    exit 0
fi

if [ "$1" == "uninstall" ]; then
    echo "=========================================="
    echo "    Antigravity Feishu Bot 彻底卸载脚本"
    echo "=========================================="
    read -p "⚠️ 警告：此操作将彻底删除所有相关后台服务和项目源码文件。确定要继续卸载吗？[y/N]: " confirm_uninstall
    if [[ ! "$confirm_uninstall" =~ ^[Yy]$ ]]; then
        echo "✅ 已取消卸载操作。"
        exit 0
    fi

    echo "🛑 正在停止后台服务..."
    if command -v pm2 &> /dev/null; then
        pm2 delete feishu-bot || true
        pm2 delete agy-daemon || true
        pm2 save || true
    else
        echo "⚠️ 未检测到 PM2，跳过服务清理。"
    fi
    echo "✅ 后台服务已停止并移除。"
    
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
    if [[ "$(basename "$SCRIPT_DIR")" == "antigravity-feishu-bot" ]]; then
        cd ..
        echo "🗑️ 正在彻底删除项目源码目录: $SCRIPT_DIR ..."
        rm -rf "$SCRIPT_DIR"
    elif [ -d "$SCRIPT_DIR/antigravity-feishu-bot" ]; then
        echo "🗑️ 正在彻底删除项目源码目录: $SCRIPT_DIR/antigravity-feishu-bot ..."
        rm -rf "$SCRIPT_DIR/antigravity-feishu-bot"
    fi
    
    if [ -f "install.sh" ]; then
        rm -f install.sh
    fi
    
    # 只移除本项目注册的 PM2 服务，绝不递归删除整个 ~/.pm2
    # （其中可能包含用户其他应用的服务配置与日志）
    echo "ℹ️ 已停止并移除本项目注册的 PM2 服务。"
    echo "ℹ️ 如需清理 ~/.pm2 中的其他 PM2 应用与历史日志，请手动执行：rm -rf ~/.pm2"
    
    echo "✅ 彻底卸载完成！项目所有文件及后台服务已被完全清除。"
    exit 0
fi
# --- 1. 欢迎与环境检测 ---
if [ "$EUID" -eq 0 ]; then
    echo "❌ 错误: 发现你正在使用 root 权限 (sudo) 运行此脚本！"
    echo "为了避免权限错乱和安全风险，请【不要】使用 sudo 执行本脚本。"
    echo "只需作为普通用户运行 (./install.sh)，脚本在最后配置开机自启时会自动向你索要临时授权。"
    exit 1
fi

echo "=========================================="
echo "    Antigravity Feishu Bot 一键部署脚本"
echo "=========================================="
echo ""

read -p "即将开始检测环境并自动部署飞书机器人，确定要继续吗？[Y/n]: " confirm_install
if [[ "$confirm_install" =~ ^[Nn]$ ]]; then
    echo "✅ 已取消部署操作。"
    exit 0
fi

if ! command -v python3 &> /dev/null; then
    echo "❌ 错误: 未检测到 python3，请先安装 Python 3.10+"
    exit 1
fi

if ! command -v pm2 &> /dev/null; then
    echo "⚠️ 未检测到 pm2，准备尝试自动安装 Node.js, npm 和 pm2..."
    
    if ! command -v npm &> /dev/null; then
        echo "⚠️ 未检测到 npm，准备通过 apt-get 安装 nodejs 和 npm..."
        if command -v apt-get &> /dev/null; then
            echo "👉 需要 sudo 权限来安装依赖包："
            sudo apt-get update
            sudo apt-get install -y nodejs npm
        else
            echo "❌ 错误: 未找到 apt-get，无法自动安装 nodejs 和 npm。请手动安装后重试。"
            exit 1
        fi
    fi
    
    echo "⬇️ 正在全局安装 pm2..."
    sudo npm install -g pm2
    
    if ! command -v pm2 &> /dev/null; then
        echo "❌ 错误: pm2 自动安装失败，请手动执行 npm install -g pm2"
        exit 1
    fi
    echo "✅ pm2 自动安装成功。"
fi

if ! command -v agy &> /dev/null && ! command -v antigravity &> /dev/null && [ ! -f "$HOME/.local/bin/agy" ]; then
    echo "⚠️ 警告: 未检测到 Antigravity (agy) 底层引擎！飞书机器人依赖该引擎执行核心任务。"
    echo "请确保您已经正确安装了 Antigravity CLI，否则机器人将无法正常工作。"
    read -p "是否强制继续部署？[y/N]: " force_deploy
    if [[ ! "$force_deploy" =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "✅ 环境检测通过 (python3, pm2, agy)"
echo ""

# --- 2. 源码获取与更新 ---
REPO_URL="https://github.com/Level6me/antigravity-feishu-bot.git"
if [ ! -f "main.py" ]; then
    echo "⚠️ 未在当前目录检测到核心文件 (main.py)，准备克隆或更新代码仓库..."
    if [ ! -d "antigravity-feishu-bot" ]; then
        echo "⬇️ 正在从 GitHub 克隆项目..."
        git clone $REPO_URL
    fi
    cd antigravity-feishu-bot
    echo "⬇️ 正在拉取最新代码..."
    git pull origin main
else
    echo "✅ 当前目录已是项目源码根目录。"
    echo "⬇️ 尝试拉取最新代码..."
    git pull origin main || true
fi
echo ""

# --- 3. 交互式环境变量配置 ---
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

# --- 4. 配置虚拟环境与依赖 ---
echo "📦 开始配置 Python 虚拟环境并安装依赖..."
if [ ! -d "venv" ]; then
    if ! python3 -m venv venv; then
        echo "⚠️ 虚拟环境创建失败，可能是因为缺失 python3-venv。"
        if command -v apt-get &> /dev/null; then
            echo "👉 尝试通过 apt-get 自动安装 python3-venv (需要 sudo 权限)..."
            sudo apt-get update
            sudo apt-get install -y python3-venv python3.12-venv || true
            
            if ! python3 -m venv venv; then
                echo "❌ 错误: 自动安装 python3-venv 后仍无法创建虚拟环境，请手动解决。"
                exit 1
            fi
        else
            echo "❌ 错误: 未找到 apt-get，无法自动安装 python3-venv。请手动解决。"
            exit 1
        fi
    fi
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

# --- 5. PM2 启动服务 ---
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

    echo "🚀 准备启动本地语言服务器守护进程 (LSP Daemon)..."
    if pm2 status | grep -q "agy-daemon"; then
        pm2 restart agy-daemon
        echo "✅ LSP 守护进程已重启。"
    else
        pm2 start venv/bin/python3 --name "agy-daemon" -- agy_daemon.py
        echo "✅ LSP 守护进程已启动。"
    fi
    
    echo "💾 正在保存 PM2 进程列表..."
    pm2 save
    echo ""
    echo "⚙️  正在自动配置系统开机自启..."
    STARTUP_CMD=$(pm2 startup | grep 'sudo')
    if [ -n "$STARTUP_CMD" ]; then
        echo "⚠️  接下来将为您配置开机服务，可能会提示输入您的开机密码（输入时不会显示字符，按回车即可）："
        eval "$STARTUP_CMD"
        echo "✅ 开机自启配置完成！"
    fi
    echo ""
    echo "🎉 部署完成！你的飞书机器人现在应该已经上线了。"
    echo "👉 你可以使用 'pm2 logs feishu-bot' 来查看实时运行日志。"
else
    echo "⏭️ 跳过启动。你可以稍后手动运行: pm2 start venv/bin/python3 --name \"feishu-bot\" -- main.py"
fi
