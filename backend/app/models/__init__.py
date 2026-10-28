"""模型注册入口。

必须在此集中导入，`Base.metadata.create_all` 与 Alembic autogenerate 才能
发现全部表定义。
"""

from app.models.conversation import AgentRun, AgentStep, Conversation, Message
from app.models.dataset import Dataset, DatasetItem, DatasetVersion, QCRecord
from app.models.evaluation import EvalCase, EvalCaseResult, EvalRun, EvalSuite
from app.models.knowledge import Chunk, Document, KnowledgeBase
from app.models.prompt import PromptTemplate
from app.models.tool import ToolDefinition, ToolInvocation

__all__ = [
    "Conversation",
    "Message",
    "AgentRun",
    "AgentStep",
    "ToolDefinition",
    "ToolInvocation",
    "KnowledgeBase",
    "Document",
    "Chunk",
    "Dataset",
    "DatasetItem",
    "QCRecord",
    "DatasetVersion",
    "PromptTemplate",
    "EvalSuite",
    "EvalCase",
    "EvalRun",
    "EvalCaseResult",
]
