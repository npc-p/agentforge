"""路由聚合。"""

from fastapi import APIRouter

from app.api.routes import chat, dataset, evaluation, knowledge, prompts, system, tools

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(chat.router)
api_router.include_router(knowledge.router)
api_router.include_router(dataset.router)
api_router.include_router(tools.router)
api_router.include_router(prompts.router)
api_router.include_router(evaluation.router)

__all__ = ["api_router"]
