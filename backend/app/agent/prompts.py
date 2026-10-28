"""Prompt 工程层。

设计原则：
1. 所有 Prompt 集中管理，支持从数据库读取启用的版本，代码内只保留默认值兜底；
2. 每个节点在 system 首行写入 `[NODE:XXX]` 标识，既便于日志定位，也让离线 Mock
   能够确定性地分派输出；
3. 输出格式约束写进 Prompt 且在执行侧用 JSON 解析兜底，不假设模型一定听话。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt import PromptTemplate

# ---------------------------------------------------------------- 默认模板

NODE_PLAN = """[NODE:PLAN]
你是 AgentForge 的任务规划器。请把用户请求拆解为可执行的最小任务序列。

要求：
1. 只输出 JSON，不要输出任何解释性文字；
2. JSON 结构：{"intent": "意图分类", "need_tools": true/false, "tasks": [{"step": 1, "action": "工具名或answer", "desc": "任务说明"}], "reasoning": "拆解理由"}；
3. intent 取值范围：knowledge_qa / computation / data_query / web_lookup / chitchat / mixed；
4. 若无需外部信息即可回答，need_tools 置为 false，tasks 只保留一条 answer；
5. 任务数量不超过 {max_tasks} 个，且不得虚构不存在的工具。
"""

NODE_DECIDE = """[NODE:DECIDE]
你是 AgentForge 的执行器。请判断当前是否还需要调用工具获取信息。

规则：
1. 信息已足够回答用户问题时，**不要调用工具**，直接输出面向用户的完整回答；
2. 以下情形**必须**调用工具，不得心算或猜测：精确算术与百分比计算（calculator）、
   当前日期时间与星期（current_time）、文档类事实（knowledge_search）、
   数据库统计（sql_query）、外部网页事实（http_request）；
3. 需要外部事实时，调用最合适的工具，一次只调用必要的工具；
4. 严禁编造工具返回值；严禁重复调用已经成功并返回数据的同一工具同一参数；
5. 回答使用中文，结构清晰；若使用了工具结果，需在答案中体现依据。

{extra_hint}
"""

NODE_REFLECT = """[NODE:REFLECT]
你是 AgentForge 的结果校验器。给定用户问题与已获得的工具观察结果，判断信息是否充分。

只输出 JSON：{"enough": true/false, "missing": "缺少什么信息", "reason": "判定理由"}
判定标准：
- 若问题涉及具体事实、数值、文档内容，而观察结果中无对应证据，则 enough=false；
- 若问题为闲聊或通用知识，可直接回答，则 enough=true；
- 已连续调用工具仍无结果时，enough=true 并在 missing 中说明，交由回答阶段如实告知用户。
"""

NODE_ANSWER = """[NODE:ANSWER]
你是 AgentForge 的答复生成器。请基于给定的工具观察结果与参考资料回答用户问题。

要求：
1. 优先使用【观察结果】中的客观数据，不得与观察结果矛盾；
2. 若参考资料包含片段编号（如 [1]），在引用处标注编号；
3. 信息不足时明确说明「当前资料未覆盖」，不要编造；
4. 使用中文，先给结论，再给依据；必要时用列表组织，避免冗长套话；
5. 不要提及「观察结果」「参考资料」这类内部术语，直接自然作答。
"""

RAG_CONTEXT = """【参考资料】
{context}
【参考资料结束】
"""

AGENT_PERSONA = """你是 AgentForge 智能体，一个具备工具调用与知识检索能力的任务执行助手。
你的行为准则：
- 先理解意图再行动，不确定时优先检索而非猜测；
- 所有事实性结论必须可追溯到工具结果或参考资料；
- 回答简洁、结构清晰、对不确定信息显式标注。"""

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "plan": {"name": "agent.plan", "content": NODE_PLAN, "scene": "agent"},
    "decide": {"name": "agent.decide", "content": NODE_DECIDE, "scene": "agent"},
    "reflect": {"name": "agent.reflect", "content": NODE_REFLECT, "scene": "agent"},
    "answer": {"name": "agent.answer", "content": NODE_ANSWER, "scene": "agent"},
    "persona": {"name": "agent.persona", "content": AGENT_PERSONA, "scene": "agent"},
}


def render(template: str, variables: dict[str, Any] | None = None) -> str:
    """显式占位符替换。

    不用 ``str.format``：Prompt 里含 JSON 示例的花括号，format 会把 ``{"intent":...}``
    当作占位符并抛 KeyError（历史上该异常被吞掉，导致 ``{max_tasks}`` 从未被替换）。
    """
    if not variables:
        return template
    for key, value in variables.items():
        template = template.replace("{" + key + "}", str(value))
    return template


async def load_template(
    key: str,
    session: AsyncSession | None = None,
    variables: dict[str, Any] | None = None,
) -> str:
    """读取启用中的模板并填充变量；数据库不可用或为空时回退默认值。"""
    template_text = DEFAULT_TEMPLATES[key]["content"]
    name = DEFAULT_TEMPLATES[key]["name"]

    if session is not None:
        try:
            result = await session.execute(
                select(PromptTemplate)
                .where(PromptTemplate.name == name, PromptTemplate.is_active.is_(True))
                .order_by(PromptTemplate.version.desc())
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is not None and row.content.strip():
                template_text = row.content
        except Exception:  # noqa: BLE001 - 模板读取失败不应阻断主链路
            pass

    return render(template_text, variables)


def build_rag_context(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    blocks: list[str] = []
    for idx, hit in enumerate(hits, start=1):
        blocks.append(
            f"[{idx}] 出处：{hit.get('doc_name', '未知文档')}（chunk={hit.get('chunk_id')}，"
            f"相关度={round(float(hit.get('score', 0)), 3)}）\n{hit.get('content', '')}"
        )
    return RAG_CONTEXT.format(context="\n\n".join(blocks))
