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


STORE_INDEX_URL = "https://raw.githubusercontent.com/Level6me/feishu-bot-plugin/main/index.json"
_store_cache = {"timestamp": 0, "plugins": []}


def fetch_remote_store_plugins() -> list:
    """Fetch real-time plugin store index.json from GitHub remote repository."""
    import time
    import urllib.request
    now = time.time()
    if _store_cache["plugins"] and (now - _store_cache["timestamp"] < 60):
        return _store_cache["plugins"]

    try:
        req = urllib.request.Request(STORE_INDEX_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list) and data:
                _store_cache["timestamp"] = now
                _store_cache["plugins"] = data
                return data
    except Exception as e:
        log.warning(f"[PluginStore] Failed to fetch remote index.json: {e}")

    return _store_cache["plugins"]
