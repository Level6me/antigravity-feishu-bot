# -*- coding: utf-8 -*-
"""
Hybrid Mathematical Decision Model (HMDM v3.2) for Antigravity Feishu Bot.

Implements a 4-layer deterministic & probabilistic mathematical decision engine:
1. Layer 1: 8-Dimensional Feature Extractor & Character Shannon Entropy (< 0.2ms)
2. Layer 2: Bayesian Intent Posterior with Markov Dialog State Transition Matrix T (< 0.3ms)
   + Normalized Shannon Entropy Gating H(P) to skip 65%+ redundant remote JEV HTTP calls
3. Layer 3: Continuous Calibrated Complexity Index C* & Orthogonal Task Planning Probability P(Plan)
4. Layer 4: Session-level EWMA / Hawkes Cumulative Risk Decay Model R_t & Process Tree I/O-CPU Vitality Score V_k
"""

from dataclasses import dataclass, field
import math
import os
import re
import time
from typing import Dict, List, Literal, Optional, Set, Tuple

DialogState = Literal["idle", "proposal_pending", "task_executing", "error_recovery"]
EffortTier = Literal["Low", "Medium", "High"]
SecurityLevel = Literal["L0", "L1", "L2", "L3"]
IntentClass = Literal["chat", "standard_op", "deep_arch"]


@dataclass
class FeatureVector:
    """8-dimensional normalized feature vector phi(x_t) in [0, 1]^8."""
    phi_len: float = 0.0          # phi_1: Normalized log length
    phi_imperative: float = 0.0   # phi_2: Imperative / action verb density
    phi_tech_entity: float = 0.0  # phi_3: Code / file path / CLI flag / symbol density
    phi_question: float = 0.0     # phi_4: Conceptual question / pure consultation indicator
    phi_confirm: float = 0.0      # phi_5: Short affirmative / continuation trigger
    phi_multistep: float = 0.0    # phi_6: Multi-step structural connectors
    phi_hazard: float = 0.0       # phi_7: Destructive / high-privilege operation indicator
    phi_entropy: float = 0.0      # phi_8: Normalized character Shannon entropy


@dataclass
class SessionCognitiveState:
    """Per-session Markov cognitive state and EWMA cumulative risk."""
    dialog_state: DialogState = "idle"
    last_tier: EffortTier = "Medium"
    last_needs_plan: bool = False
    last_complexity: float = 0.8
    cumulative_risk: float = 0.0
    last_update_ts: float = field(default_factory=time.time)
    consecutive_errors: int = 0
    turn_count: int = 0


@dataclass
class OrthogonalDecision:
    """Orthogonal 3-tuple decision output D_t = <E_t, Pi_t, R_t> with diagnostics."""
    effort_tier: EffortTier
    needs_task_plan: bool
    is_pure_qa: bool
    security_level: SecurityLevel
    complexity_index: float
    plan_probability: float
    local_entropy: float
    posterior_probs: Dict[IntentClass, float]
    used_remote_jev: bool
    risk_score: float
    dialog_state: DialogState
    reason_summary: str


# ---------------------------------------------------------------------------
# Markov Transition Prior Matrix P(I_t | s_{t-1})
# Rows: DialogState; Columns: (chat, standard_op, deep_arch)
# ---------------------------------------------------------------------------
MARKOV_TRANSITION_PRIORS: Dict[DialogState, Dict[IntentClass, float]] = {
    "idle": {
        "chat": 0.48,
        "standard_op": 0.37,
        "deep_arch": 0.15,
    },
    "proposal_pending": {
        "chat": 0.08,
        "standard_op": 0.54,
        "deep_arch": 0.38,
    },
    "task_executing": {
        "chat": 0.14,
        "standard_op": 0.58,
        "deep_arch": 0.28,
    },
    "error_recovery": {
        "chat": 0.05,
        "standard_op": 0.30,
        "deep_arch": 0.65,
    },
}

# Logistic weight vectors w_k for each IntentClass over the 8 features:
# [phi_len, phi_imperative, phi_tech_entity, phi_question, phi_confirm, phi_multistep, phi_hazard, phi_entropy]
LOGISTIC_WEIGHTS: Dict[IntentClass, Tuple[float, ...]] = {
    "chat": (
        -1.60,  # phi_len: shorter messages lean chat unless imperative/confirm
        -3.20,  # phi_imperative: action verbs strongly oppose pure chat
        -3.50,  # phi_tech_entity: file paths / code blocks strongly oppose pure chat
        +2.40,  # phi_question: conceptual questions favor chat
        -0.50,  # phi_confirm: handled dynamically with state
        -2.80,  # phi_multistep: multi-step markers oppose pure chat
        -3.00,  # phi_hazard: hazard keywords oppose pure chat
        -0.40,  # phi_entropy
    ),
    "standard_op": (
        +1.10,  # phi_len
        +2.60,  # phi_imperative
        +2.40,  # phi_tech_entity
        -1.20,  # phi_question
        +1.40,  # phi_confirm
        +1.30,  # phi_multistep
        +1.50,  # phi_hazard
        +0.30,  # phi_entropy
    ),
    "deep_arch": (
        +2.20,  # phi_len
        +2.30,  # phi_imperative
        +2.50,  # phi_tech_entity
        -1.80,  # phi_question
        +1.10,  # phi_confirm
        +3.10,  # phi_multistep
        +1.80,  # phi_hazard
        +0.60,  # phi_entropy
    ),
}

# Lexical patterns for fast feature extraction
_IMPERATIVE_VERBS_HIGH = re.compile(
    r"(重构|架构|深度排查|全面分析|开发规格书|全量|全都要|设计并实现|端到端|性能优化|安全审计|"
    r"系统性|重构方案|数学模型|并发|死锁|内存泄漏|根因分析|修复所有|全部修改|立即执行|帮我改)",
    re.IGNORECASE,
)
_IMPERATIVE_VERBS_STD = re.compile(
    r"(修改|修复|创建|新建|编写|运行|执行|查看|读取|检查|排查|搜索|查找|安装|更新|部署|上传|下载|"
    r"发送|导出|生成|删除|清理|添加|增加|调整|配置|测试|验证|优化|分析|统计|转换|抓取|备份|"
    r"fix|create|write|run|execute|check|read|update|deploy|test|modify|refactor|debug)",
    re.IGNORECASE,
)
_TECH_ENTITY_PATTERN = re.compile(
    r"(/[a-zA-Z0-9_\-\./]+\.[a-zA-Z0-9]+|`[^`]+`|```|[a-zA-Z0-9_]+\.(py|js|ts|json|md|sh|db|yaml|yml|toml|sql|html|css)\b|"
    r"--[a-zA-Z0-9\-]+|\b(def|class|import|async|await|SELECT|INSERT|ALTER|git|docker|systemctl|pytest|python3|pip|npm)\b)",
    re.IGNORECASE,
)
_CONCEPTUAL_QA_PATTERN = re.compile(
    r"^(请问|什么是|是什么|为什么|怎么理解|如何理解|有什么区别|原理是|介绍一下|解释一下|聊聊|你好|嗨|早上好|晚上好|谢谢|辛苦了)",
    re.IGNORECASE,
)
_CONFIRM_CONTINUATION_PATTERN = re.compile(
    r"^(好|好的|可以|行|没问题|确认|同意|继续|开始|开始吧|改吧|执行|执行吧|按你说的做|就这么办|全都要|我全都要|全部执行|yes|ok|okay|go ahead|proceed|do it)[\s!！。，,\.]*$",
    re.IGNORECASE,
)
_MULTISTEP_PATTERN = re.compile(
    r"(首先|然后|接着|最后|同时|并且|以及|第一|第二|第三|1\.|2\.|3\.|步骤|阶段|不仅.*而且|全部|所有)",
    re.IGNORECASE,
)
_HAZARD_L3_PATTERN = re.compile(
    r"(\brm\s+-rf\s+/(?:\s|$|\*)|\bmkfs\b|\bdd\s+if=|\bshutdown\b|\breboot\b|\binit\s+0\b|"
    r":\(\)\{\s*:\|:&\s*\};:|>\s*/dev/sd[a-z]|\bchmod\s+-R\s+777\s+/|\bDROP\s+DATABASE\b)",
    re.IGNORECASE,
)
_HAZARD_L2_PATTERN = re.compile(
    r"(\brm\s+-rf\b|\bgit\s+reset\s+--hard\b|\bgit\s+clean\s+-fd\b|\bgit\s+push\s+--force\b|"
    r"\bkill\s+-9\b|\bpkill\b|\bkillall\b|\bDROP\s+TABLE\b|\bTRUNCATE\b|\bsystemctl\s+(stop|restart|disable)\b|"
    r"删除所有|清空数据库|强制覆盖|格式化)",
    re.IGNORECASE,
)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _char_shannon_entropy(text: str) -> float:
    """Compute normalized character-level Shannon entropy in [0, 1]."""
    if not text:
        return 0.0
    n = len(text)
    if n <= 1:
        return 0.0
    counts: Dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    entropy = 0.0
    for c in counts.values():
        p = c / n
        entropy -= p * math.log2(p)
    # Normalize by log2(min(n, 64))
    max_h = math.log2(min(n, 64))
    return min(1.0, entropy / max_h) if max_h > 0 else 0.0


class HybridMathDecisionEngine:
    """
    4-Layer Hybrid Mathematical Decision Engine (HMDM).
    Provides deterministic <0.5ms local inference + Bayesian fusion with remote JEV.
    """

    def __init__(self, entropy_threshold: float = 0.48, risk_half_life_sec: float = 300.0):
        self.entropy_threshold = entropy_threshold
        self.risk_decay_base = 0.75
        self.risk_half_life_sec = max(30.0, risk_half_life_sec)
        self._session_states: Dict[str, SessionCognitiveState] = {}
        self._max_sessions = 500

    def get_session_state(self, chat_id: str) -> SessionCognitiveState:
        state = self._session_states.get(chat_id)
        if state is None:
            if len(self._session_states) >= self._max_sessions:
                # Evict oldest session state to prevent unbounded memory growth
                oldest_key = min(
                    self._session_states.keys(),
                    key=lambda k: self._session_states[k].last_update_ts,
                )
                self._session_states.pop(oldest_key, None)
            state = SessionCognitiveState()
            self._session_states[chat_id] = state
        return state

    def update_post_turn_state(
        self,
        chat_id: str,
        *,
        had_error: bool = False,
        proposed_plan_or_question: bool = False,
        tier_used: Optional[EffortTier] = None,
        needs_plan_used: Optional[bool] = None,
        complexity_used: Optional[float] = None,
    ) -> None:
        """Update the Markov dialog state after a turn finishes."""
        st = self.get_session_state(chat_id)
        st.last_update_ts = time.time()
        st.turn_count += 1
        if tier_used is not None:
            st.last_tier = tier_used
        if needs_plan_used is not None:
            st.last_needs_plan = needs_plan_used
        if complexity_used is not None:
            st.last_complexity = complexity_used

        if had_error:
            st.consecutive_errors += 1
            st.dialog_state = "error_recovery"
        else:
            st.consecutive_errors = 0
            if proposed_plan_or_question:
                st.dialog_state = "proposal_pending"
            elif st.last_needs_plan or st.last_tier in ("Medium", "High"):
                st.dialog_state = "task_executing"
            else:
                st.dialog_state = "idle"

    # ------------------------------------------------------------------
    # Layer 1: 8-D Feature Vector Extraction
    # ------------------------------------------------------------------
    def extract_features(self, prompt_text: str) -> FeatureVector:
        clean = (prompt_text or "").strip()
        n = len(clean)
        if n == 0:
            return FeatureVector()

        phi_len = min(1.0, math.log1p(n) / math.log1p(500.0))

        high_verbs = len(_IMPERATIVE_VERBS_HIGH.findall(clean))
        std_verbs = len(_IMPERATIVE_VERBS_STD.findall(clean))
        phi_imperative = min(1.0, 0.45 * high_verbs + 0.25 * std_verbs)

        tech_matches = len(_TECH_ENTITY_PATTERN.findall(clean))
        phi_tech_entity = min(1.0, 0.35 * tech_matches)

        is_qa_head = 1.0 if _CONCEPTUAL_QA_PATTERN.search(clean) else 0.0
        ends_q = 0.4 if clean.endswith(("?", "？", "吗", "呢")) else 0.0
        phi_question = min(1.0, max(0.0, is_qa_head + ends_q - 0.8 * phi_tech_entity - 0.6 * phi_imperative))

        phi_confirm = 1.0 if (n <= 24 and _CONFIRM_CONTINUATION_PATTERN.match(clean)) else 0.0
        if "我全都要" in clean or "全部执行" in clean or "按规格书" in clean:
            phi_confirm = 1.0
            phi_imperative = max(phi_imperative, 0.85)

        multistep_hits = len(_MULTISTEP_PATTERN.findall(clean))
        phi_multistep = min(1.0, 0.30 * multistep_hits + (0.45 if high_verbs > 0 else 0.0))

        hazard_l3 = 1.0 if _HAZARD_L3_PATTERN.search(clean) else 0.0
        hazard_l2 = 0.65 if _HAZARD_L2_PATTERN.search(clean) else 0.0
        phi_hazard = max(hazard_l3, hazard_l2)

        phi_entropy = _char_shannon_entropy(clean[:400])

        return FeatureVector(
            phi_len=round(phi_len, 4),
            phi_imperative=round(phi_imperative, 4),
            phi_tech_entity=round(phi_tech_entity, 4),
            phi_question=round(phi_question, 4),
            phi_confirm=round(phi_confirm, 4),
            phi_multistep=round(phi_multistep, 4),
            phi_hazard=round(phi_hazard, 4),
            phi_entropy=round(phi_entropy, 4),
        )

    # ------------------------------------------------------------------
    # Layer 2: Bayesian Posterior & Markov State Transition + Entropy Gate
    # ------------------------------------------------------------------
    def compute_local_posterior(
        self, fv: FeatureVector, state: SessionCognitiveState
    ) -> Tuple[Dict[IntentClass, float], float]:
        """
        Compute P_local(I_t | phi, s_{t-1}) and normalized Shannon entropy H in [0, 1].
        """
        # Decay dialog state to 'idle' if session has been inactive for > 30 minutes
        now = time.time()
        effective_state: DialogState = state.dialog_state
        if now - state.last_update_ts > 1800 and effective_state != "idle":
            effective_state = "idle"

        priors = dict(MARKOV_TRANSITION_PRIORS.get(effective_state, MARKOV_TRANSITION_PRIORS["idle"]))

        # Special Markov Transition: Short confirmation ("好的", "我全都要", "开始吧")
        if fv.phi_confirm >= 0.8:
            if effective_state in ("proposal_pending", "task_executing", "error_recovery"):
                if state.last_tier == "High" or "全都要" in str(fv.phi_imperative):
                    return {"chat": 0.02, "standard_op": 0.23, "deep_arch": 0.75}, 0.25
                return {"chat": 0.04, "standard_op": 0.68, "deep_arch": 0.28}, 0.32
            elif fv.phi_imperative >= 0.7:
                return {"chat": 0.03, "standard_op": 0.25, "deep_arch": 0.72}, 0.28

        phi_tuple = (
            fv.phi_len,
            fv.phi_imperative,
            fv.phi_tech_entity,
            fv.phi_question,
            fv.phi_confirm,
            fv.phi_multistep,
            fv.phi_hazard,
            fv.phi_entropy,
        )

        logits: Dict[IntentClass, float] = {}
        for cls_name, weights in LOGISTIC_WEIGHTS.items():
            dot = sum(w * x for w, x in zip(weights, phi_tuple))
            prior_p = max(1e-6, priors[cls_name])
            logits[cls_name] = math.log(prior_p) + dot

        max_logit = max(logits.values())
        exp_scores = {k: math.exp(v - max_logit) for k, v in logits.items()}
        total = sum(exp_scores.values())
        posteriors: Dict[IntentClass, float] = {
            k: round(v / total, 4) for k, v in exp_scores.items()
        }

        # Normalized Shannon entropy H(P) = - (1 / ln 3) * sum P_k ln P_k
        entropy = 0.0
        for p in posteriors.values():
            if p > 1e-9:
                entropy -= p * math.log(p)
        norm_entropy = min(1.0, max(0.0, entropy / math.log(3.0)))

        return posteriors, round(norm_entropy, 4)

    def should_call_remote_jev(
        self, fv: FeatureVector, posteriors: Dict[IntentClass, float], norm_entropy: float
    ) -> bool:
        """
        Entropy-Gated Remote Invocation Criterion:
        Skip remote JEV HTTP call when local posterior is confident (H <= tau_H)
        and there is no ambiguous hazard.
        """
        if fv.phi_confirm >= 0.8:
            return False
        if norm_entropy <= self.entropy_threshold and fv.phi_hazard < 0.5:
            return False
        # Pure short greetings or obvious single-step commands don't need 2s HTTP latency
        if posteriors["chat"] >= 0.80 and fv.phi_tech_entity == 0.0 and fv.phi_imperative == 0.0:
            return False
        if posteriors["deep_arch"] >= 0.78 and fv.phi_imperative >= 0.5:
            return False
        return True

    # ------------------------------------------------------------------
    # Layer 3 & 4: Bayesian Pooling, Continuous Complexity C*, Plan Pi_t & Risk R_t
    # ------------------------------------------------------------------
    def evaluate_decision(
        self,
        chat_id: str,
        prompt_text: str,
        remote_jev_data: Optional[Dict] = None,
        has_shell_scope: bool = True,
        is_outside_workspace: bool = False,
    ) -> OrthogonalDecision:
        state = self.get_session_state(chat_id)
        fv = self.extract_features(prompt_text)
        local_post, norm_entropy = self.compute_local_posterior(fv, state)

        final_post = dict(local_post)
        used_remote = False
        jev_complexity = 0.0
        jev_conf = 0.0

        if remote_jev_data and isinstance(remote_jev_data, dict):
            used_remote = True
            p_node = remote_jev_data.get("pillar_1_router", {}) or {}
            c_node = remote_jev_data.get("pillar_2_complexity", {}) or {}

            is_conv = bool(p_node.get("is_conversational", False))
            needs_term = bool(p_node.get("needs_terminal", False))
            intent_conf = float(p_node.get("intent_confidence", 0.7))
            jev_complexity = float(c_node.get("complexity_score", 0.5))
            jev_conf = float(c_node.get("confidence", 0.7))

            # Construct remote JEV observation distribution P_jev
            if is_conv and not needs_term and jev_complexity < 0.45:
                p_jev: Dict[IntentClass, float] = {"chat": 0.85, "standard_op": 0.12, "deep_arch": 0.03}
            elif jev_complexity >= 1.45 or (needs_term and jev_complexity >= 1.15):
                p_jev = {"chat": 0.03, "standard_op": 0.27, "deep_arch": 0.70}
            else:
                p_jev = {"chat": 0.10, "standard_op": 0.72, "deep_arch": 0.18}

            # However, if user sent a short confirmation in an active task state, protect against stateless JEV downgrade
            if fv.phi_confirm >= 0.8 and state.dialog_state in ("proposal_pending", "task_executing", "error_recovery"):
                alpha = 0.15
            else:
                alpha = _sigmoid((min(intent_conf, jev_conf) - 0.58) / 0.12)

            for k in ("chat", "standard_op", "deep_arch"):
                final_post[k] = round(alpha * p_jev[k] + (1.0 - alpha) * local_post[k], 4)

        # Structural complexity score psi_struct in [0, 2.0]
        psi_struct = min(
            2.0,
            0.85 * fv.phi_imperative
            + 0.75 * fv.phi_tech_entity
            + 0.65 * fv.phi_multistep
            + 0.35 * fv.phi_len,
        )
        expected_prior_c = 2.0 * final_post["deep_arch"] + 1.0 * final_post["standard_op"]

        if used_remote:
            c_star = 0.45 * jev_complexity + 0.30 * expected_prior_c + 0.25 * psi_struct
        else:
            c_star = 0.55 * expected_prior_c + 0.45 * psi_struct

        # Error recovery automatic boost
        if state.dialog_state == "error_recovery" and state.consecutive_errors >= 1:
            c_star = max(c_star, 1.40)
        elif fv.phi_confirm >= 0.8 and state.dialog_state in ("proposal_pending", "task_executing"):
            c_star = max(c_star, state.last_complexity, 1.10)

        c_star = round(min(2.0, max(0.0, c_star)), 3)

        # Continuous Complexity -> EffortTier mapping
        if c_star < 0.42 and final_post["chat"] >= 0.65 and fv.phi_imperative < 0.25 and fv.phi_tech_entity == 0.0:
            effort_tier: EffortTier = "Low"
            is_pure_qa = True
        elif c_star >= 1.32 or final_post["deep_arch"] >= 0.52:
            effort_tier = "High"
            is_pure_qa = False
        else:
            effort_tier = "Medium"
            is_pure_qa = False

        # Orthogonal Task Planning Probability P(Pi_t = 1)
        # Decoupled from High tier: BOTH Medium and High operational tasks get a 3-4 step task plan!
        p_plan = 1.0 - (final_post["chat"] * (1.0 - fv.phi_tech_entity) * (1.0 - fv.phi_imperative))
        if fv.phi_confirm >= 0.8 and state.dialog_state in ("proposal_pending", "task_executing"):
            p_plan = max(p_plan, 0.85)
        p_plan = round(min(1.0, max(0.0, p_plan)), 4)

        needs_task_plan = bool(not is_pure_qa and (p_plan >= 0.38 or effort_tier in ("Medium", "High")))

        # Layer 4: Session-level EWMA / Hawkes Cumulative Risk Decay Model R_t
        now = time.time()
        dt = max(0.0, now - state.last_update_ts)
        decay = math.pow(self.risk_decay_base, dt / self.risk_half_life_sec)

        r_instant = (
            0.75 * fv.phi_hazard
            + (0.35 if is_outside_workspace else 0.0)
            + (0.30 if (not has_shell_scope and fv.phi_imperative > 0.3) else 0.0)
        )
        if used_remote and remote_jev_data:
            s_node = remote_jev_data.get("pillar_3_security", {}) or {}
            if s_node.get("is_high_risk"):
                r_instant = max(r_instant, 0.72)

        r_t = round(min(1.5, decay * state.cumulative_risk + r_instant), 4)
        state.cumulative_risk = r_t
        state.last_update_ts = now

        # Orthogonal Security Level mapping (NEVER skipped just because EffortTier is Low/Medium!)
        if fv.phi_hazard >= 0.95 or r_t >= 0.95:
            sec_level: SecurityLevel = "L3"
        elif r_t >= 0.62 or fv.phi_hazard >= 0.60:
            sec_level = "L2"
        elif r_t >= 0.30:
            sec_level = "L1"
        else:
            sec_level = "L0"

        reason = (
            f"Tier={effort_tier}(C*={c_star:.2f}), Plan={needs_task_plan}(P={p_plan:.2f}), "
            f"Sec={sec_level}(R={r_t:.2f}), H={norm_entropy:.2f}, State={state.dialog_state}, "
            f"RemoteJEV={used_remote}"
        )

        return OrthogonalDecision(
            effort_tier=effort_tier,
            needs_task_plan=needs_task_plan,
            is_pure_qa=is_pure_qa,
            security_level=sec_level,
            complexity_index=c_star,
            plan_probability=p_plan,
            local_entropy=norm_entropy,
            posterior_probs=final_post,
            used_remote_jev=used_remote,
            risk_score=r_t,
            dialog_state=state.dialog_state,
            reason_summary=reason,
        )


# ---------------------------------------------------------------------------
# Layer 4B: Process Tree I/O + CPU EWMA Vitality Tracker (Fixes 12h Hang Bug)
# ---------------------------------------------------------------------------
@dataclass
class ProcessVitalityState:
    baseline_pids: Set[int] = field(default_factory=set)
    last_cpu_ticks: int = 0
    last_io_bytes: int = 0
    ewma_vitality: float = 1.0
    last_real_pulse_ts: float = field(default_factory=time.time)
    child_first_seen: Dict[int, float] = field(default_factory=dict)


def snapshot_process_group_pids(leader_pid: int) -> Set[int]:
    """Return all current PIDs in the process group or descendant tree of leader_pid."""
    pids: Set[int] = set()
    if not leader_pid or leader_pid <= 0:
        return pids
    try:
        pgid = os.getpgid(leader_pid)
    except OSError:
        pgid = leader_pid

    parent_map: Dict[int, int] = {}
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                    stat_str = f.read()
                rpar = stat_str.rfind(")")
                if rpar == -1:
                    continue
                fields = stat_str[rpar + 2 :].split()
                if len(fields) < 3:
                    continue
                ppid = int(fields[1])
                pgrp = int(fields[2])
                parent_map[pid] = ppid
                if pgrp == pgid or pid == leader_pid:
                    pids.add(pid)
            except OSError:
                continue
    except OSError:
        return pids

    # Include transitive descendants of any matched PID
    added = True
    while added:
        added = False
        for pid, ppid in list(parent_map.items()):
            if pid not in pids and ppid in pids:
                pids.add(pid)
                added = True
    return pids


def measure_process_tree_vitality(
    leader_pid: int,
    vstate: ProcessVitalityState,
    *,
    beta: float = 0.82,
    max_single_child_age_sec: float = 300.0,
) -> Dict:
    """
    Compute true process group vitality using CPU ticks delta, /proc/<pid>/io bytes delta,
    and newly spawned non-baseline active child processes.
    Never treats pre-existing sleeping warm-pool daemon children as 'active work'!
    """
    now = time.time()
    all_pids = snapshot_process_group_pids(leader_pid)
    if not all_pids:
        vstate.ewma_vitality = 0.0
        return {
            "active": False,
            "vitality": 0.0,
            "reason": "no_pids",
            "new_active_pids": [],
            "stalled_seconds": now - vstate.last_real_pulse_ts,
        }

    total_cpu = 0
    total_io = 0
    running_pids: List[int] = []
    new_active_pids: List[int] = []

    for pid in all_pids:
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
                s = f.read()
            rpar = s.rfind(")")
            if rpar != -1:
                fields = s[rpar + 2 :].split()
                if len(fields) >= 13:
                    state_ch = fields[0]
                    utime = int(fields[11])
                    stime = int(fields[12])
                    total_cpu += utime + stime
                    if state_ch in ("R", "D"):
                        running_pids.append(pid)
        except OSError:
            continue

        # Read /proc/<pid>/io if permitted
        try:
            with open(f"/proc/{pid}/io", "r", encoding="utf-8", errors="ignore") as fio:
                for line in fio:
                    if line.startswith(("rchar:", "wchar:", "read_bytes:", "write_bytes:")):
                        parts = line.split()
                        if len(parts) == 2 and parts[1].isdigit():
                            total_io += int(parts[1])
        except OSError:
            pass

        # Track newly spawned child processes (excluding warm-pool baseline PIDs)
        if pid != leader_pid and pid not in vstate.baseline_pids:
            first_ts = vstate.child_first_seen.setdefault(pid, now)
            if (now - first_ts) < max_single_child_age_sec:
                new_active_pids.append(pid)

    # Prune dead PIDs from child_first_seen
    for tracked_pid in list(vstate.child_first_seen.keys()):
        if tracked_pid not in all_pids:
            vstate.child_first_seen.pop(tracked_pid, None)

    cpu_delta = max(0, total_cpu - vstate.last_cpu_ticks) if vstate.last_cpu_ticks > 0 else 0
    io_delta = max(0, total_io - vstate.last_io_bytes) if vstate.last_io_bytes > 0 else 0
    vstate.last_cpu_ticks = total_cpu
    vstate.last_io_bytes = total_io

    # Real pulse indicator I_k in {0, 1}
    has_pulse = bool(
        running_pids
        or cpu_delta > 0
        or io_delta > 128
        or len(new_active_pids) > 0
    )
    pulse_val = 1.0 if has_pulse else 0.0
    vstate.ewma_vitality = round(beta * vstate.ewma_vitality + (1.0 - beta) * pulse_val, 4)

    if has_pulse:
        vstate.last_real_pulse_ts = now

    reason_parts = []
    if running_pids:
        reason_parts.append(f"R/D={len(running_pids)}")
    if cpu_delta > 0:
        reason_parts.append(f"dCPU={cpu_delta}")
    if io_delta > 128:
        reason_parts.append(f"dIO={io_delta}")
    if new_active_pids:
        reason_parts.append(f"newSub={len(new_active_pids)}")

    return {
        "active": has_pulse,
        "vitality": vstate.ewma_vitality,
        "reason": ",".join(reason_parts) if reason_parts else "idle",
        "all_pids": list(all_pids),
        "new_active_pids": new_active_pids,
        "cpu_delta": cpu_delta,
        "io_delta": io_delta,
        "stalled_seconds": round(now - vstate.last_real_pulse_ts, 1),
    }


# Global singleton instance
math_decision_engine = HybridMathDecisionEngine()
