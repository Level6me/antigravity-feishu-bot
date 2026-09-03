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
            # Check if this is a monorepo containing plugins/<plugin_id>/manifest.json or plugins/*/manifest.json
            sub_plugin_dir = None
            if plugin_id:
                candidate = os.path.join(target_dir, "plugins", plugin_id)
                if os.path.exists(os.path.join(candidate, "manifest.json")):
                    sub_plugin_dir = candidate

            if not sub_plugin_dir:
                plugins_parent = os.path.join(target_dir, "plugins")
                if os.path.exists(plugins_parent) and os.path.isdir(plugins_parent):
                    for sub in os.listdir(plugins_parent):
                        candidate = os.path.join(plugins_parent, sub)
                        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "manifest.json")):
                            sub_plugin_dir = candidate
                            break

            if sub_plugin_dir and os.path.exists(os.path.join(sub_plugin_dir, "manifest.json")):
                import time
                temp_extract = os.path.join(PLUGINS_DIR, f"_tmp_{plugin_id}_{int(time.time())}")
                shutil.move(sub_plugin_dir, temp_extract)
                shutil.rmtree(target_dir, ignore_errors=True)
                shutil.move(temp_extract, target_dir)
                manifest_path = os.path.join(target_dir, "manifest.json")

        if not os.path.exists(manifest_path):
            shutil.rmtree(target_dir, ignore_errors=True)
            return False, "克隆成功但仓库根目录下未找到 `manifest.json` 插件规范文件！"

        # Auto-install requirements.txt if present
        _install_plugin_requirements(target_dir)

        return True, f"插件 `{plugin_id}` 从 GitHub 安装成功！"

    except subprocess.TimeoutExpired:
        shutil.rmtree(target_dir, ignore_errors=True)
        return False, "GitHub 仓库拉取超时 (30s)，请检查网络连接。"
    except Exception as e:
        shutil.rmtree(target_dir, ignore_errors=True)
        return False, f"安装异常: {e}"


def _install_plugin_requirements(target_dir: str):
    """Automatically pip install requirements.txt if present in plugin directory."""
    req_file = os.path.join(target_dir, "requirements.txt")
    if os.path.exists(req_file):
        import sys
        venv_python = os.path.join(BASE_DIR, "venv", "bin", "python3")
        python_bin = venv_python if os.path.exists(venv_python) else sys.executable
        try:
            log.info(f"[PluginStore] Installing dependencies from {req_file} using {python_bin}")
            subprocess.run([python_bin, "-m", "pip", "install", "-r", req_file], capture_output=True, timeout=60, check=True)
        except Exception as e:
            log.error(f"[PluginStore] Failed to install requirements for {target_dir}: {e}")


def _restart_plugin_daemon_if_needed(target_dir: str):
    """If plugin manifest specifies a daemon_script or config specifies daemon_name, reload its PM2 service."""
    manifest_path = os.path.join(target_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        daemon_script = manifest.get("daemon_script")
        plugin_id = manifest.get("id", "")
        if daemon_script or plugin_id == "cron_scheduler":
            daemon_name = "feishu-cron-daemon" if plugin_id == "cron_scheduler" else f"feishu-{plugin_id.replace('_', '-')}-daemon"
            subprocess.Popen(["pm2", "restart", daemon_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log.warning(f"[PluginStore] Failed to restart daemon for {target_dir}: {e}")


def update_plugin(plugin_id: str) -> tuple[bool, str]:
    """Pull latest code for an installed git-based plugin or sync from official monorepo."""
    target_dir = os.path.join(PLUGINS_DIR, plugin_id)
    if not os.path.exists(target_dir):
        return False, f"插件 `{plugin_id}` 不存在。"

    git_dir = os.path.join(target_dir, ".git")
    if os.path.exists(git_dir):
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
                _install_plugin_requirements(target_dir)
                _restart_plugin_daemon_if_needed(target_dir)
                return True, f"插件 `{plugin_id}` 代码同步更新完成！\n`{res.stdout.strip()}`"
            else:
                return False, f"更新失败: {res.stderr.strip()}"

        except subprocess.TimeoutExpired:
            return False, "Git 更新拉取超时 (20s)。"
        except Exception as e:
            return False, f"更新异常: {e}"

    # 检查本地官方插件仓库源
    official_repo_dir = os.environ.get("FEISHU_BOT_PLUGIN_DIR") or os.path.abspath(os.path.join(BASE_DIR, "..", "feishu-bot-plugin"))
    official_plugin_dir = os.path.join(official_repo_dir, "plugins", plugin_id)
    if os.path.exists(official_plugin_dir):
        pull_info = ""
        try:
            custom_env = os.environ.copy()
            custom_env["GIT_TERMINAL_PROMPT"] = "0"
            pull_res = subprocess.run(
                ["git", "pull", "--no-rebase"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=official_repo_dir,
                env=custom_env
            )
            if pull_res.returncode == 0 and pull_res.stdout.strip():
                pull_info = f"\nGit 状态: `{pull_res.stdout.strip()}`"
        except Exception:
            pass

        try:
            shutil.copytree(official_plugin_dir, target_dir, dirs_exist_ok=True)
            _install_plugin_requirements(target_dir)
            _restart_plugin_daemon_if_needed(target_dir)
            return True, f"插件 `{plugin_id}` 已成功从官方插件仓库同步至最新版本！{pull_info}"
        except Exception as e:
            return False, f"从官方插件仓库同步失败: {e}"

    return False, f"插件 `{plugin_id}` 不是由独立 Git 仓库安装，且未在官方插件中心找到对应源码。"


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

CACHE_FILE = os.path.join(BASE_DIR, "remote_plugin_cache.json")
_store_cache = {"timestamp": 0, "plugins": None}


def load_remote_store_cache() -> list:
    """Load cached remote plugin store list from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                plugins = json.load(f)
                if isinstance(plugins, list) and plugins:
                    return plugins
        except Exception as e:
            log.error(f"[PluginStore] Error reading cache file {CACHE_FILE}: {e}")
    return DEFAULT_FEATURED_PLUGINS


def save_remote_store_cache(plugins: list):
    """Save remote plugin store list to disk for permanent persistence."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(plugins, f, ensure_ascii=False, indent=2)
        log.info(f"[PluginStore] Saved {len(plugins)} remote plugins to {CACHE_FILE}")
    except Exception as e:
        log.error(f"[PluginStore] Error saving cache file {CACHE_FILE}: {e}")


def fetch_remote_store_plugins(force_refresh: bool = False) -> list:
    """Fetch plugin list from GitHub plugins/ subdirectories without index.json."""
    import urllib.request

    if not force_refresh:
        if _store_cache["plugins"]:
            return _store_cache["plugins"]
        disk_plugins = load_remote_store_cache()
        if disk_plugins:
            _store_cache["plugins"] = disk_plugins
            return disk_plugins

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
                save_remote_store_cache(fetched)
                log.info(f"[PluginStore] Refreshed remote store plugins: {len(fetched)} plugins found.")
                return fetched
    except Exception as e:
        log.warning(f"[PluginStore] Failed to scan GitHub plugins directory: {e}")

    disk_plugins = load_remote_store_cache()
    _store_cache["plugins"] = disk_plugins
    return disk_plugins
