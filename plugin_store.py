"""Plugin Store and Lifecycle Management for antigravity-feishu-bot."""

import os
import shutil
import json
import subprocess
from logger import log
from plugin_base import BasePlugin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
SOURCES_FILE = os.path.join(BASE_DIR, "plugin_sources.json")

DEFAULT_SOURCES = [
    {
        "id": "official",
        "name": "🌟 官方精选插件源",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "Antigravity 团队官方维护的插件仓库中心"
    }
]


def load_plugin_sources() -> list:
    """Load configured plugin sources."""
    if not os.path.exists(SOURCES_FILE):
        save_plugin_sources(DEFAULT_SOURCES)
        return DEFAULT_SOURCES
    try:
        with open(SOURCES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"[PluginStore] Error loading sources: {e}")
        return DEFAULT_SOURCES


def save_plugin_sources(sources: list):
    """Save plugin sources list to JSON file."""
    try:
        with open(SOURCES_FILE, "w", encoding="utf-8") as f:
            json.dump(sources, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[PluginStore] Error saving sources: {e}")


def add_plugin_source(name: str, repo_url: str, description: str = "") -> bool:
    """Add a new GitHub repository plugin source."""
    sources = load_plugin_sources()
    for s in sources:
        if s.get("repo_url") == repo_url:
            return False
    sources.append({
        "id": f"src_{int(os.path.getmtime(PLUGINS_DIR) if os.path.exists(PLUGINS_DIR) else 0)}",
        "name": name,
        "repo_url": repo_url,
        "description": description
    })
    save_plugin_sources(sources)
    return True


def install_plugin_from_github(repo_url: str, custom_id: str = "") -> tuple[bool, str]:
    """Clone a GitHub repository into plugins/ directory."""
    if not repo_url.startswith("http://") and not repo_url.startswith("https://") and not repo_url.startswith("git@"):
        # Support shorthand like "owner/repo"
        if "/" in repo_url and not repo_url.startswith("http"):
            repo_url = f"https://github.com/{repo_url}.git"

    repo_name = repo_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    plugin_id = custom_id if custom_id else repo_name
    target_dir = os.path.join(PLUGINS_DIR, plugin_id)

    if os.path.exists(target_dir):
        return False, f"插件目录 `{plugin_id}` 已存在，若要重装请先卸载。"

    custom_env = os.environ.copy()
    custom_env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        log.info(f"[PluginStore] Cloning plugin from {repo_url} to {target_dir}")
        res = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, target_dir],
            capture_output=True,
            text=True,
            timeout=30,
            env=custom_env
        )

        if res.returncode != 0:
            return False, f"Git Clone 失败: {res.stderr.strip()}"

        manifest_path = os.path.join(target_dir, "manifest.json")
        if not os.path.exists(manifest_path):
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, "克隆成功但仓库根目录下未找到 `manifest.json` 插件规范文件！"

        return True, f"插件 `{plugin_id}` 从 GitHub 安装成功！"

    except subprocess.TimeoutExpired:
        shutil.rmtree(target_dir, ignore_errors=True)
        return False, "GitHub 仓库拉取超时 (30s)，请检查网络连接。"
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return False, f"安装异常: {e}"


def update_plugin(plugin_id: str) -> tuple[bool, str]:
    """Pull latest code for an installed git-based plugin."""
    target_dir = os.path.join(PLUGINS_DIR, plugin_id)
    if not os.path.exists(target_dir):
        return False, f"插件 `{plugin_id}` 不存在。"

    git_dir = os.path.join(target_dir, ".git")
    if not os.path.exists(git_dir):
        return False, f"插件 `{plugin_id}` 不是由 Git 仓库安装，无法通过 Git 更新。"

    custom_env = os.environ.copy()
    custom_env["GIT_TERMINAL_PROMPT"] = "0"

    try:
        res = subprocess.run(
            ["git", "pull", "origin", "main"],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=target_dir,
            env=custom_env
        )
        if res.returncode != 0:
            res = subprocess.run(
                ["git", "pull"],
                capture_output=True,
                text=True,
                timeout=20,
                cwd=target_dir,
                env=custom_env
            )

        if res.returncode == 0:
            return True, f"插件 `{plugin_id}` 代码同步更新完成！\n`{res.stdout.strip()}`"
        else:
            return False, f"更新失败: {res.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return False, "Git 更新拉取超时 (20s)。"
    except Exception as e:
        return False, f"更新异常: {e}"


def uninstall_plugin(plugin_id: str) -> tuple[bool, str]:
    """Remove plugin directory physically."""
    target_dir = os.path.join(PLUGINS_DIR, plugin_id)
    if not os.path.exists(target_dir):
        return False, f"插件 `{plugin_id}` 不存在。"

    try:
        shutil.rmtree(target_dir, ignore_errors=True)
        return True, f"插件 `{plugin_id}` 已成功从物理磁盘卸载移除！"
    except Exception as e:
        return False, f"卸载异常: {e}"


DEFAULT_FEATURED_PLUGINS = [
    {
        "id": "server_health",
        "name": "🖥️ 服务器巡检与健康报告",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "监控 CPU 负载、内存率、磁盘余量，发送 /sysinfo 即可查看"
    },
    {
        "id": "cron_scheduler",
        "name": "⏱️ 计划任务与定时调度",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "基于 Cron 表达式与秒级倒计时的后台定时任务与巡检调度中心"
    },
    {
        "id": "ai_memory",
        "name": "🧠 AI 长期记忆管理",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "管理个人的长期对话偏好与全局 AI 记忆库，并在大模型对话前自动注入"
    },
    {
        "id": "notes_manager",
        "name": "📝 备忘录与随手记",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "随时快速记录、列出与管理个人的随手记、灵感与工作备忘条目"
    },
    {
        "id": "system_updater",
        "name": "🔄 系统在线热更新",
        "repo_url": "https://github.com/Level6me/feishu-bot-plugin",
        "description": "检查并拉取 Git 云端最新代码版本，一键自动构建热重启机器人引擎"
    }
]

_store_cache = {"timestamp": 0, "plugins": DEFAULT_FEATURED_PLUGINS}


def fetch_remote_store_plugins(force_refresh: bool = False) -> list:
    """Fetch plugin list from GitHub plugins/ subdirectories without index.json."""
    import urllib.request
    if not force_refresh and _store_cache["plugins"]:
        return _store_cache["plugins"]

    url = "https://api.github.com/repos/Level6me/feishu-bot-plugin/contents/plugins"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            items = json.loads(resp.read().decode('utf-8'))
            fetched = []
            for item in items:
                if item.get("type") == "dir":
                    pid = item.get("name")
                    m_url = f"https://raw.githubusercontent.com/Level6me/feishu-bot-plugin/main/plugins/{pid}/manifest.json"
                    try:
                        m_req = urllib.request.Request(m_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(m_req, timeout=4) as m_resp:
                            manifest = json.loads(m_resp.read().decode('utf-8'))
                            manifest["repo_url"] = "https://github.com/Level6me/feishu-bot-plugin"
                            fetched.append(manifest)
                    except Exception as e:
                        log.warning(f"[PluginStore] Error loading manifest for {pid}: {e}")
            if fetched:
                _store_cache["plugins"] = fetched
                log.info(f"[PluginStore] Refreshed remote store plugins: {len(fetched)} plugins found.")
                return fetched
    except Exception as e:
        log.warning(f"[PluginStore] Failed to scan GitHub plugins directory: {e}")

    return _store_cache["plugins"] or DEFAULT_FEATURED_PLUGINS
