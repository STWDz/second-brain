from aiogram import Router

from bot.handlers.commands import router as commands_router
from bot.handlers.content import router as content_router
from bot.handlers.extras import router as extras_router
from bot.handlers.inline import router as inline_router
from bot.handlers.voice import router as voice_router

main_router = Router()
main_router.include_router(commands_router)
main_router.include_router(extras_router)
main_router.include_router(voice_router)
main_router.include_router(inline_router)
main_router.include_router(content_router)
