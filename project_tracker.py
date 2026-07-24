import os
import datetime
import logging

log = logging.getLogger("feishu_bot")

TRACKER_FILENAME = ".project_track.secret.md"

TRACKER_TEMPLATE = """# 项目追踪与全盘档案 (`.project_track.secret.md`)

## 📌 项目基本信息与技术栈
- 项目名称: {proj_name}
- 架构/依赖: 未指定

## 📊 当前进度与最后状态
- 当前状态: 开发运行中
- 最后更新时间: {now_str}

## 📝 计划与 TodoList
- [ ] 初始开发任务

## 🔐 敏感凭据与服务器配置 (仅限本项目隐秘使用，严禁外泄)
- 服务器 IP: 
- SSH 端口/账号/密码/密钥: 
- API Key / Token / AppID / Secret: 

## 🛠️ 历史改动履历与决策记录
- ✅ 保留方案:
- 🗑️ 废弃/无用的试错方案:

## ⚠️ 已知问题与缺陷
- 无
"""

def ensure_and_read_project_tracker(proj_path: str) -> str:
    """
    1. 确保项目的 .gitignore 包含 .project_track.secret.md 防范误提交 GitHub
    2. 确保项目根目录存在 .project_track.secret.md (若无则自动根据模板初始化)
    3. 读取并返回文档文本
    """
    if not proj_path or proj_path in ["默认", "Default"] or not os.path.isdir(proj_path):
        return ""

    tracker_path = os.path.join(proj_path, TRACKER_FILENAME)
    gitignore_path = os.path.join(proj_path, ".gitignore")

    # 1. 自动写入 .gitignore 彻底隔离提交
    try:
        if os.path.exists(gitignore_path):
            with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                gi_content = f.read()
            if TRACKER_FILENAME not in gi_content and "*.secret.md" not in gi_content:
                with open(gitignore_path, "a", encoding="utf-8") as f:
                    if not gi_content.endswith("\n"):
                        f.write("\n")
                    f.write(f"\n# Confidential project tracker\n{TRACKER_FILENAME}\n*.secret.md\n")
                log.info(f"Automatically added {TRACKER_FILENAME} to {gitignore_path}")
        else:
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write(f"# Confidential project tracker\n{TRACKER_FILENAME}\n*.secret.md\n")
            log.info(f"Created .gitignore with {TRACKER_FILENAME}")
    except Exception as e:
        log.error(f"Error checking/updating .gitignore in {proj_path}: {e}")

    # 2. 初始化 .project_track.secret.md
    try:
        if not os.path.exists(tracker_path):
            proj_name = os.path.basename(os.path.abspath(proj_path))
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            initial_content = TRACKER_TEMPLATE.format(proj_name=proj_name, now_str=now_str)
            with open(tracker_path, "w", encoding="utf-8") as f:
                f.write(initial_content)
            log.info(f"Initialized new project tracker at {tracker_path}")
    except Exception as e:
        log.error(f"Error creating project tracker at {tracker_path}: {e}")

    # 3. 读取文档文本
    try:
        if os.path.exists(tracker_path):
            with open(tracker_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read().strip()
    except Exception as e:
        log.error(f"Error reading project tracker at {tracker_path}: {e}")

    return ""
