from aiogram import Router

from bot.handlers.admin import router as admin_router
from bot.handlers.commands import router as commands_router
from bot.handlers.content import router as content_router
from bot.handlers.extras import router as extras_router
from bot.handlers.inline import router as inline_router
from bot.handlers.notion import router as notion_router
from bot.handlers.tts import router as tts_router
from bot.handlers.voice import router as voice_router

main_router = Router()
main_router.include_router(admin_router)
main_router.include_router(commands_router)
main_router.include_router(extras_router)
main_router.include_router(tts_router)
main_router.include_router(notion_router)
main_router.include_router(voice_router)
main_router.include_router(inline_router)
main_router.include_router(content_router)
