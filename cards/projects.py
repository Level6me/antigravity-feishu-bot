"""Card builders: projects."""

import os
import re
from datetime import datetime
from config import WORKSPACE_ROOT
from logger import log

from cards.common import create_footer

def _quick_git_branch(path: str) -> str:
    """Read `.git/HEAD` directly in <0.1ms without spawning a git subprocess."""
    try:
        head_file = os.path.join(path, ".git", "HEAD")
        if os.path.isfile(head_file):
            with open(head_file, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read(256).strip()
            if raw.startswith("ref: refs/heads/"):
                return raw[len("ref: refs/heads/"):]
            return raw[:8]
    except Exception:
        pass
    return ""


def build_dir_browser_card(
    active_project_path,
    recent_projects=None,
    recent_page=1,
    workspace_root=None,
    ignored_projects=None,
    active_form="",
    notice="",
):
    elements = []

    proj_root = workspace_root if workspace_root else WORKSPACE_ROOT
    active_branch = _quick_git_branch(active_project_path) if (active_project_path and os.path.isdir(str(active_project_path))) else ""
    branch_badge = f"  ·  🌿 `{active_branch}`" if active_branch else ""

    header_md = (
        f"⚙️ **公共项目根目录**：`{proj_root}`\n"
        f"📂 **当前活跃工作区**：`{active_project_path}`{branch_badge}"
    )
    if notice:
        header_md += f"\n\n💡 {notice}"
    elements.append({"tag": "markdown", "content": header_md})

    elements.append({
        "tag": "action",
        "layout": "flow",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔼 收起新建框" if active_form == "create" else "➕ 新建 / 克隆项目"},
                "type": "primary" if active_form != "create" else "default",
                "value": {
                    "action": "toggle_project_form",
                    "form": "" if active_form == "create" else "create",
                    "page": recent_page,
                },
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "🔼 收起路径框" if active_form == "root" else "⚙️ 设置根目录"},
                "type": "default" if active_form != "root" else "primary",
                "value": {
                    "action": "toggle_project_form",
                    "form": "" if active_form == "root" else "root",
                    "page": recent_page,
                },
            },
        ],
    })

    if active_form == "create":
        elements.append({
            "tag": "form",
            "name": "project_create_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "project_target",
                    "label": {"tag": "plain_text", "content": "新项目名称、Git 仓库 URL 或已有目录绝对路径"},
                    "placeholder": {"tag": "plain_text", "content": "例如：my-new-app 或 https://github.com/org/repo.git"},
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "auto",
                            "elements": [
                                {
                                    "tag": "button",
                                    "name": "proj_create_submit",
                                    "text": {"tag": "plain_text", "content": "✅ 创建并切换工作区"},
                                    "type": "primary",
                                    "action_type": "form_submit",
                                    "value": {"action": "project_create_submit", "parent_path": proj_root},
                                }
                            ],
                        }
                    ],
                },
            ],
        })
    elif active_form == "root":
        elements.append({
            "tag": "form",
            "name": "project_root_form",
            "elements": [
                {
                    "tag": "input",
                    "name": "workspace_root_input",
                    "default_value": str(proj_root),
                    "label": {"tag": "plain_text", "content": "公共项目根目录绝对路径"},
                    "placeholder": {"tag": "plain_text", "content": "例如：/home/ubuntu/projects"},
                },
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "auto",
                            "elements": [
                                {
                                    "tag": "button",
                                    "name": "proj_root_submit",
                                    "text": {"tag": "plain_text", "content": "✅ 保存根目录"},
                                    "type": "primary",
                                    "action_type": "form_submit",
                                    "value": {"action": "project_root_submit"},
                                }
                            ],
                        }
                    ],
                },
            ],
        })

    elements.append({"tag": "hr"})

    # 扫描项目根目录 proj_root 下的所有子文件夹，并将当前活跃项目与最近常用项目置顶
    scanned_map = {}
    try:
        for name in os.listdir(proj_root):
            if name.startswith('.') or name in ["venv", "downloads", "__pycache__", "node_modules"]:
                continue
            full_path = os.path.join(proj_root, name)
            if ignored_projects and full_path in ignored_projects:
                continue
            if os.path.isdir(full_path):
                scanned_map[full_path] = name
    except Exception as e:
        log.error(f"Failed to scan project root: {e}")

    if recent_projects:
        for p in recent_projects:
            if ignored_projects and p in ignored_projects:
                continue
            if os.path.exists(p) and os.path.isdir(p) and p != "/" and p not in scanned_map:
                scanned_map[p] = os.path.basename(p) or p

    recent_rank = {p: idx for idx, p in enumerate(recent_projects or [])}

    def _proj_sort_key(item):
        name, full_p = item
        is_act = 0 if full_p == active_project_path else 1
        rec_idx = recent_rank.get(full_p, 999)
        return (is_act, rec_idx, name.lower())

    all_projects = sorted(
        [(nm, fp) for fp, nm in scanned_map.items()],
        key=_proj_sort_key,
    )

    if all_projects:
        items_per_page = 6
        total_items = len(all_projects)
        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
        page = max(1, min(int(recent_page or 1), total_pages))

        elements.append({
            "tag": "markdown",
            "content": f"📁 **项目选择列表** (第 {page}/{total_pages} 页 · 共 {total_items} 个，常用置顶)："
        })

        start_idx = (page - 1) * items_per_page
        page_items = all_projects[start_idx:start_idx + items_per_page]

        for name, p in page_items:
            is_active = (p == active_project_path)
            is_recent = p in recent_rank and not is_active
            p_branch = _quick_git_branch(p)
            br_tag = f" `({p_branch})`" if p_branch else ""
            if is_active:
                name_display = f"🌟 **{name} (当前活跃)**{br_tag}"
            elif is_recent:
                name_display = f"🕘 **{name}**{br_tag}"
            else:
                name_display = f"📁 **{name}**{br_tag}"

            columns = [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": f"{name_display}\n*`{p}`*"
                        }
                    ]
                }
            ]

            if not is_active:
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "size": "small",
                            "text": {"tag": "plain_text", "content": "切换"},
                            "type": "primary",
                            "value": {"action": "select_project", "path": p, "page": page}
                        }
                    ]
                })
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "size": "small",
                            "text": {"tag": "plain_text", "content": "🙈 隐藏"},
                            "type": "default",
                            "confirm": {
                                "title": {"tag": "plain_text", "content": "从列表隐藏项目"},
                                "text": {"tag": "plain_text", "content": f"仅将「{name}」移出飞书面板列表，不会删除磁盘上的任何代码文件。确定隐藏吗？"}
                            },
                            "value": {"action": "remove_project_from_list", "path": p, "page": page}
                        }
                    ]
                })
            else:
                columns.append({
                    "tag": "column",
                    "width": "auto",
                    "vertical_align": "center",
                    "elements": [
                        {
                            "tag": "button",
                            "size": "small",
                            "text": {"tag": "plain_text", "content": "✅ 当前活跃"},
                            "type": "default",
                            "disabled": True,
                        }
                    ]
                })

            elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "columns": columns
            })

        page_actions = []
        if page > 1:
            page_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "◀️ 上一页"},
                "type": "default",
                "value": {"action": "browse_recent_page", "page": page - 1, "current_path": active_project_path}
            })
        if page < total_pages:
            page_actions.append({
                "tag": "button",
                "text": {"tag": "plain_text", "content": "下一页 ▶️"},
                "type": "default",
                "value": {"action": "browse_recent_page", "page": page + 1, "current_path": active_project_path}
            })

        if page_actions:
            elements.append({
                "tag": "action",
                "actions": page_actions
            })
    else:
        elements.append({
            "tag": "markdown",
            "content": "📭 *当前没有可用的项目，请点击上方「➕ 新建 / 克隆项目」创建！*"
        })

    elements.append(create_footer())

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"content": "📁 项目管理器", "tag": "plain_text"}
        },
        "elements": elements
    }
