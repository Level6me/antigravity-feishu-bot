"""Antigravity CLI (`agy 1.5.x`) Skills discovery and lifecycle manager.

Discovers skills across Antigravity's hierarchical customization roots:
  1. Workspace skills: `<workspace>/.agents/skills/<name>/SKILL.md` (and `.agents/skills.json`)
  2. Global skills: `~/.gemini/config/skills/`, `~/.gemini/antigravity-cli/skills/`, `~/.gemini/skills/`
  3. Built-in skills: `~/.gemini/antigravity-cli/builtin/skills/`

Supports skill search, enable/disable toggling (for workspace & global skills),
online custom `SKILL.md` creation, GitHub / local directory skill installation,
deletion, and session-level active skill pinning (`active_skills`).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import config
from logger import log

_SAFE_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?\Z", re.DOTALL)


def is_valid_skill_name(name: str) -> bool:
    s = (name or "").strip()
    return bool(s and not s.startswith("-") and _SAFE_SKILL_NAME_RE.match(s))


def parse_skill_markdown(raw_text: str, fallback_name: str = "") -> Dict[str, str]:
    """Parse YAML frontmatter (`name`, `description`) and instruction body from `SKILL.md`."""
    text = (raw_text or "").strip()
    name = fallback_name
    desc_lines: List[str] = []
    body = text

    m = _FRONTMATTER_RE.match(text)
    if m:
        fm_text = m.group(1) or ""
        body = (m.group(2) or "").strip()
        lines = fm_text.splitlines()
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            stripped = line.strip()
            if stripped.startswith("name:"):
                val = stripped[len("name:") :].strip().strip("\"'")
                if val:
                    name = val
                idx += 1
            elif stripped.startswith("description:"):
                val = stripped[len("description:") :].strip()
                if val in (">-", ">", "|", "|-"):
                    idx += 1
                    while idx < len(lines):
                        sub_line = lines[idx]
                        if sub_line.startswith((" ", "\t")) or not sub_line.strip():
                            if sub_line.strip():
                                desc_lines.append(sub_line.strip())
                            idx += 1
                        else:
                            break
                else:
                    val_clean = val.strip("\"'")
                    if val_clean:
                        desc_lines.append(val_clean)
                    idx += 1
                    while idx < len(lines) and lines[idx].startswith(("  ", "\t")):
                        cont = lines[idx].strip()
                        if cont:
                            desc_lines.append(cont)
                        idx += 1
            else:
                idx += 1

    description = " ".join(desc_lines).strip()
    if not description and body:
        # Fallback: first non-heading paragraph in body
        for bline in body.splitlines():
            s = bline.strip()
            if s and not s.startswith(("#", "```", "---")):
                description = s[:220]
                break

    return {
        "name": (name or fallback_name).strip(),
        "description": description,
        "body": body,
    }


def _expand_skills_json_entries(skills_json_path: str, base_root: str) -> List[str]:
    """Resolve explicit skill directory paths from a `skills.json` file."""
    if not skills_json_path or not os.path.isfile(skills_json_path):
        return []
    dirs: List[str] = []
    try:
        with open(skills_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        for entry in (data.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            raw_p = str(entry.get("path") or "").strip()
            if not raw_p:
                continue
            if raw_p.startswith("~/"):
                resolved = os.path.expanduser(raw_p)
            elif os.path.isabs(raw_p):
                resolved = raw_p
            else:
                cand_dot = os.path.join(os.path.dirname(skills_json_path), raw_p)
                resolved = cand_dot if os.path.isdir(cand_dot) else os.path.join(base_root, raw_p)
            if os.path.isdir(resolved):
                dirs.append(os.path.abspath(resolved))
    except Exception:
        pass
    return dirs


def _candidate_skill_roots(
    workspace_dir: Optional[str] = None,
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> List[Tuple[str, str]]:
    """Return ordered `(scope, directory_path)` pairs matching Antigravity discovery priority."""
    if extra_roots is not None:
        return list(extra_roots)

    roots: List[Tuple[str, str]] = []
    home = os.path.expanduser("~")
    agy_home = config.get_antigravity_home()

    if workspace_dir and os.path.isdir(workspace_dir):
        ws_abs = os.path.abspath(workspace_dir)
        for dot in (".agents", ".agent", "_agents", "_agent"):
            dot_dir = os.path.join(ws_abs, dot)
            s_dir = os.path.join(dot_dir, "skills")
            roots.append(("workspace", s_dir))
            for extra_d in _expand_skills_json_entries(os.path.join(dot_dir, "skills.json"), ws_abs):
                roots.append(("workspace", extra_d))

    # Global user skill roots
    cfg_dir = os.path.join(home, ".gemini", "config")
    roots.append(("global", os.path.join(cfg_dir, "skills")))
    for extra_g in _expand_skills_json_entries(os.path.join(cfg_dir, "skills.json"), cfg_dir):
        roots.append(("global", extra_g))
    roots.append(("global", os.path.join(agy_home, "skills")))
    roots.append(("global", os.path.join(home, ".gemini", "skills")))

    # Built-in skills bundled with Antigravity CLI
    roots.append(("builtin", os.path.join(agy_home, "builtin", "skills")))
    return roots


def _inspect_skill_dir(skill_dir: str, scope: str) -> Optional[Dict[str, Any]]:
    if not skill_dir or not os.path.isdir(skill_dir):
        return None
    active_md = os.path.join(skill_dir, "SKILL.md")
    disabled_md = os.path.join(skill_dir, "SKILL.md.disabled")
    if os.path.isfile(active_md):
        md_path = active_md
        enabled = True
    elif os.path.isfile(disabled_md):
        md_path = disabled_md
        enabled = False
    else:
        return None

    dir_name = os.path.basename(os.path.normpath(skill_dir))
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            raw_content = f.read(32768)
    except Exception:
        raw_content = ""

    parsed = parse_skill_markdown(raw_content, fallback_name=dir_name)
    skill_name = parsed["name"] or dir_name
    assets: List[str] = []
    for sub in ("scripts", "references", "examples", "resources"):
        sub_p = os.path.join(skill_dir, sub)
        if os.path.isdir(sub_p):
            try:
                if os.listdir(sub_p):
                    assets.append(sub)
            except Exception:
                pass

    body_preview = (parsed.get("body") or "").strip()
    if len(body_preview) > 360:
        body_preview = body_preview[:360] + "…"

    return {
        "name": skill_name,
        "dir_name": dir_name,
        "description": parsed.get("description") or "(未提供描述)",
        "body_preview": body_preview,
        "scope": scope,  # workspace | global | builtin
        "enabled": enabled,
        "path": md_path,
        "dir_path": skill_dir,
        "assets": assets,
        "all_dirs": [skill_dir],
    }


def list_skills(
    workspace_dir: Optional[str] = None,
    search_query: str = "",
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Discover and deduplicate all Antigravity skills across workspace, global, and builtin roots."""
    roots = _candidate_skill_roots(workspace_dir=workspace_dir, extra_roots=extra_roots)
    by_key: Dict[str, Dict[str, Any]] = {}
    seen_real_dirs = set()

    for scope, root_path in roots:
        if not root_path or not os.path.isdir(root_path):
            continue
        try:
            entries = sorted(os.listdir(root_path))
        except Exception:
            continue
        for entry in entries:
            s_dir = os.path.join(root_path, entry)
            if not os.path.isdir(s_dir):
                continue
            real_dir = os.path.realpath(s_dir)
            info = _inspect_skill_dir(s_dir, scope=scope)
            if not info:
                continue
            key = info["name"].lower()
            if key not in by_key:
                by_key[key] = info
                seen_real_dirs.add(real_dir)
            else:
                existing = by_key[key]
                if real_dir not in seen_real_dirs:
                    existing["all_dirs"].append(s_dir)
                    seen_real_dirs.add(real_dir)
                # If any active copy in same scope is enabled, reflect accurately
                if existing["scope"] == scope and info["enabled"] and not existing["enabled"]:
                    existing["enabled"] = True
                    existing["path"] = info["path"]

    scope_rank = {"workspace": 0, "global": 1, "builtin": 2}
    items = sorted(
        by_key.values(),
        key=lambda s: (scope_rank.get(str(s.get("scope", "global")), 9), str(s.get("name", "")).lower()),
    )

    q = (search_query or "").strip().lower()
    if q:
        items = [
            s
            for s in items
            if q in str(s.get("name", "")).lower()
            or q in str(s.get("dir_name", "")).lower()
            or q in str(s.get("description", "")).lower()
        ]
    return items


def get_skill_info(
    name: str,
    workspace_dir: Optional[str] = None,
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    target = (name or "").strip().lower()
    if not target:
        return None
    for s in list_skills(workspace_dir=workspace_dir, extra_roots=extra_roots):
        if s["name"].lower() == target or s["dir_name"].lower() == target:
            return s
    return None


def set_skill_enabled(
    name: str,
    enabled: bool,
    workspace_dir: Optional[str] = None,
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[bool, str]:
    """Enable or disable a workspace/global skill by toggling `SKILL.md` <-> `SKILL.md.disabled` across all matching copies."""
    info = get_skill_info(name, workspace_dir=workspace_dir, extra_roots=extra_roots)
    if not info:
        return False, f"未找到技能：`{name}`"
    if info.get("scope") == "builtin":
        return False, f"内置技能 `{info['name']}` 随 Antigravity CLI 核心提供，不支持停用；您可通过「📌 挂载」控制会话级显式激活。"

    toggled_count = 0
    for s_dir in info.get("all_dirs", [info["dir_path"]]):
        if "/builtin/skills/" in s_dir.replace("\\", "/"):
            continue
        active_p = os.path.join(s_dir, "SKILL.md")
        disabled_p = os.path.join(s_dir, "SKILL.md.disabled")
        try:
            if enabled:
                if os.path.isfile(disabled_p) and not os.path.isfile(active_p):
                    os.replace(disabled_p, active_p)
                    toggled_count += 1
                elif os.path.isfile(active_p):
                    toggled_count += 1
            else:
                if os.path.isfile(active_p):
                    os.replace(active_p, disabled_p)
                    toggled_count += 1
                elif os.path.isfile(disabled_p):
                    toggled_count += 1
        except Exception as e:
            return False, f"切换技能状态失败：{e}"

    if toggled_count > 0:
        verb = "已启用" if enabled else "已禁用"
        return True, f"{verb}技能 `{info['name']}`"
    return False, f"未能更新技能 `{info['name']}` 状态"


def create_custom_skill(
    name: str,
    description: str,
    body: str,
    *,
    scope: str = "workspace",
    workspace_dir: Optional[str] = None,
    global_skills_root: Optional[str] = None,
) -> Tuple[bool, str]:
    """Create or update a custom `SKILL.md` in workspace `.agents/skills/` or global skills directory."""
    clean_name = (name or "").strip().lower().replace(" ", "-")
    clean_desc = (description or "").strip()
    clean_body = (body or "").strip()

    if not is_valid_skill_name(clean_name):
        return False, "技能名称仅支持字母、数字、点、下划线与短横线（1~64 字符）。"
    if not clean_desc:
        return False, "请填写技能触发描述（description），以便 Agent 判断何时激活该技能。"

    if scope == "workspace" and workspace_dir and os.path.isdir(workspace_dir):
        base_root = os.path.join(os.path.abspath(workspace_dir), ".agents", "skills")
        scope_label = "当前工作区 (`.agents/skills`)"
    else:
        base_root = global_skills_root or os.path.join(config.get_antigravity_home(), "skills")
        scope_label = "全局技能库"

    skill_dir = os.path.join(base_root, clean_name)
    skill_md_path = os.path.join(skill_dir, "SKILL.md")
    disabled_path = os.path.join(skill_dir, "SKILL.md.disabled")

    if not clean_body:
        clean_body = f"# {clean_name}\n\n{clean_desc}\n"
    elif not clean_body.lstrip().startswith("#"):
        clean_body = f"# {clean_name}\n\n{clean_body}\n"

    desc_indented = "\n  ".join(line.strip() for line in clean_desc.splitlines() if line.strip())
    md_content = (
        f"---\n"
        f"name: {clean_name}\n"
        f"description: >-\n"
        f"  {desc_indented}\n"
        f"---\n\n"
        f"{clean_body.strip()}\n"
    )

    try:
        os.makedirs(skill_dir, exist_ok=True)
        if os.path.isfile(disabled_path):
            os.remove(disabled_path)
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(md_content)
        return True, f"已在{scope_label}创建/更新技能 `{clean_name}`"
    except Exception as e:
        return False, f"写入技能文件失败：{e}"


def remove_custom_skill(
    name: str,
    workspace_dir: Optional[str] = None,
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[bool, str]:
    """Delete a custom workspace or global skill directory (protects builtin skills)."""
    info = get_skill_info(name, workspace_dir=workspace_dir, extra_roots=extra_roots)
    if not info:
        return False, f"未找到技能：`{name}`"
    if info.get("scope") == "builtin":
        return False, f"内置技能 `{info['name']}` 不可物理删除。"

    removed_dirs = 0
    for s_dir in info.get("all_dirs", [info["dir_path"]]):
        if "/builtin/skills/" in s_dir.replace("\\", "/"):
            continue
        if os.path.isdir(s_dir):
            try:
                shutil.rmtree(s_dir)
                removed_dirs += 1
            except Exception as e:
                return False, f"删除技能目录失败：{e}"

    if removed_dirs > 0:
        return True, f"已移除技能 `{info['name']}`"
    return False, f"未能移除技能 `{info['name']}`"


def _collect_skill_dirs_from_folder(src_dir: str) -> List[str]:
    """Find skill directory/directories inside `src_dir` containing `SKILL.md`."""
    if not os.path.isdir(src_dir):
        return []
    if os.path.isfile(os.path.join(src_dir, "SKILL.md")):
        return [src_dir]

    found: List[str] = []
    # Check direct subdirectories and `.agents/skills/*` or `skills/*`
    search_bases = [
        src_dir,
        os.path.join(src_dir, "skills"),
        os.path.join(src_dir, ".agents", "skills"),
    ]
    seen = set()
    for base in search_bases:
        if not os.path.isdir(base):
            continue
        if os.path.isfile(os.path.join(base, "SKILL.md")) and base not in seen:
            found.append(base)
            seen.add(base)
            continue
        try:
            for entry in sorted(os.listdir(base)):
                sub = os.path.join(base, entry)
                if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, "SKILL.md")) and sub not in seen:
                    found.append(sub)
                    seen.add(sub)
        except Exception:
            pass
    return found


async def install_skill_from_source(
    source: str,
    *,
    scope: str = "global",
    workspace_dir: Optional[str] = None,
    global_skills_root: Optional[str] = None,
) -> Tuple[bool, str]:
    """Install one or more skills from a GitHub URL (including `/tree/<branch>/<subpath>`) or local directory."""
    clean_src = (source or "").strip()
    if not clean_src or clean_src.startswith("-"):
        return False, "请提供有效的 GitHub 仓库 URL 或本地技能目录路径。"

    if scope == "workspace" and workspace_dir and os.path.isdir(workspace_dir):
        dest_root = os.path.join(os.path.abspath(workspace_dir), ".agents", "skills")
        scope_label = "当前工作区 (`.agents/skills`)"
    else:
        dest_root = global_skills_root or os.path.join(config.get_antigravity_home(), "skills")
        scope_label = "全局技能库"

    expanded_local = os.path.abspath(os.path.expanduser(clean_src))
    if os.path.isdir(expanded_local):
        skill_dirs = _collect_skill_dirs_from_folder(expanded_local)
        if not skill_dirs:
            return False, f"目录 `{expanded_local}` 下未找到有效的 `SKILL.md` 文件。"
        os.makedirs(dest_root, exist_ok=True)
        installed_names: List[str] = []
        for s_dir in skill_dirs[:25]:
            s_name = os.path.basename(os.path.normpath(s_dir))
            target_dir = os.path.join(dest_root, s_name)
            if os.path.abspath(s_dir) != os.path.abspath(target_dir):
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                shutil.copytree(s_dir, target_dir)
            installed_names.append(s_name)
        return True, f"已从本地目录安装 {len(installed_names)} 个技能至{scope_label}：`{', '.join(installed_names[:8])}`"

    # Support GitHub URLs including `/tree/<branch>/<subpath>`
    gh_tree_match = re.match(
        r"^(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/tree/([^/]+)/(.*)$",
        clean_src,
        re.IGNORECASE,
    )
    if gh_tree_match:
        repo_url = gh_tree_match.group(1)
        branch = gh_tree_match.group(2)
        subpath = gh_tree_match.group(3).strip("/")
    elif re.match(r"^(https?://|git@)[^\s]+$", clean_src, re.IGNORECASE):
        repo_url = clean_src
        branch = ""
        subpath = ""
    else:
        return False, f"未找到本地目录或合法的 Git 仓库地址：`{clean_src}`"

    tmp_dir = tempfile.mkdtemp(prefix="agy_skill_install_")
    try:
        clone_cmd = ["git", "clone", "--depth", "1"]
        if branch and not branch.startswith("-"):
            clone_cmd.extend(["--branch", branch])
        clone_cmd.extend([repo_url, tmp_dir])

        proc = await asyncio.create_subprocess_exec(
            *clone_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err_b = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        if proc.returncode != 0:
            err_txt = (err_b or b"").decode("utf-8", errors="replace").strip()[:240]
            return False, f"克隆技能仓库失败：{err_txt}"

        inspect_root = os.path.join(tmp_dir, subpath) if subpath else tmp_dir
        if not os.path.exists(inspect_root):
            return False, f"仓库中不存在子路径：`{subpath}`"

        skill_dirs = _collect_skill_dirs_from_folder(inspect_root)
        if not skill_dirs:
            return False, "该仓库/路径中未找到包含 `SKILL.md` 的技能目录。"

        os.makedirs(dest_root, exist_ok=True)
        installed_names = []
        for s_dir in skill_dirs[:30]:
            s_name = os.path.basename(os.path.normpath(s_dir))
            if s_dir == tmp_dir:
                s_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
            s_name = re.sub(r"[^A-Za-z0-9._-]", "-", s_name)
            target_dir = os.path.join(dest_root, s_name)
            if os.path.exists(target_dir):
                shutil.rmtree(target_dir)
            shutil.copytree(s_dir, target_dir)
            gt_dir = os.path.join(target_dir, ".git")
            if os.path.isdir(gt_dir):
                shutil.rmtree(gt_dir, ignore_errors=True)
            installed_names.append(s_name)

        return True, f"已从 GitHub 安装 {len(installed_names)} 个技能至{scope_label}：`{', '.join(installed_names[:8])}`"
    except asyncio.TimeoutError:
        return False, "克隆 GitHub 技能仓库超时（60s）。"
    except Exception as e:
        log.warning(f"[SkillManager] install_skill_from_source failed: {e}")
        return False, f"安装技能异常：{e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_active_skills_prompt_block(
    active_skill_names: List[str],
    workspace_dir: Optional[str] = None,
    extra_roots: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """Build a prompt directive block for skills explicitly pinned to the current chat session."""
    if not active_skill_names:
        return ""
    resolved_lines: List[str] = []
    for raw_name in active_skill_names:
        info = get_skill_info(raw_name, workspace_dir=workspace_dir, extra_roots=extra_roots)
        if info and info.get("enabled", True) and os.path.isfile(info.get("path", "")):
            resolved_lines.append(
                f"- **{info['name']}** (`{info['path']}`): {info.get('description', '')}"
            )
    if not resolved_lines:
        return ""
    return (
        "[Active Mounted Antigravity Skills / 当前会话用户已显式挂载激活的技能]\n"
        "用户在飞书技能面板中显式挂载了以下 Antigravity Skill，若当前任务与该技能相关，请务必优先使用 `read_file` 阅读对应 `SKILL.md` 并严格遵循其规范执行：\n"
        + "\n".join(resolved_lines)
        + "\n\n"
    )
