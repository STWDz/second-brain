"""Reply-keyboard menu router.

Turns the most-used slash commands into tap-buttons, with an FSM flow for
the three actions that need free-text input (ask / search / chat).

Routing contract with the rest of the bot:
    * This router MUST be registered BEFORE `content_router`, otherwise the
      catch-all ``F.text`` handler in content.py would swallow every tap.
    * Button handlers clear any FSM state so tapping a different button
      while mid-input transitions cleanly.
    * The BTN_CANCEL handler works in every state.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import settings
from bot.keyboards import (
    BTN_ASK,
    BTN_CANCEL,
    BTN_CHAT,
    BTN_HELP,
    BTN_PINNED,
    BTN_QUIZ,
    BTN_RANDOM,
    BTN_SEARCH,
    BTN_STATS,
    BTN_TAGS,
    MENU_BUTTONS,
    cancel_menu,
    main_menu,
)

logger = logging.getLogger(__name__)
router = Router()


class MenuStates(StatesGroup):
    """Three input flows driven by menu buttons."""

    waiting_ask = State()
    waiting_search = State()
    waiting_chat = State()


# ── /menu — re-summon the reply keyboard if it got dismissed ──────────────

@router.message(Command("menu"))
async def cmd_menu(message: types.Message, state: FSMContext) -> None:
    """Brings the menu back in case the user hid it with Telegram's arrow."""
    await state.clear()
    await message.answer(
        "📍 Головне меню",
        reply_markup=main_menu(settings.webapp_url or None),
    )


# ── Cancel (works in any state, and also no-op if no state) ──────────────

@router.message(F.text == BTN_CANCEL)
async def btn_cancel(message: types.Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        # User tapped cancel out of the blue — just reshow the menu silently.
        await message.answer(
            "📍 Головне меню", reply_markup=main_menu(settings.webapp_url or None)
        )
        return
    await state.clear()
    await message.answer(
        "Скасовано.", reply_markup=main_menu(settings.webapp_url or None)
    )


# ── Stateless action buttons ─────────────────────────────────────────────
# Each just delegates to the underlying command handler so all logic stays
# in one place and the command + button paths behave identically.

@router.message(F.text == BTN_RANDOM)
async def btn_random(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.extras import cmd_random
    await cmd_random(message)


@router.message(F.text == BTN_QUIZ)
async def btn_quiz(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.extras import cmd_quiz
    await cmd_quiz(message)


@router.message(F.text == BTN_PINNED)
async def btn_pinned(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.extras import cmd_pinned
    await cmd_pinned(message)


@router.message(F.text == BTN_STATS)
async def btn_stats(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.extras import cmd_stats
    await cmd_stats(message)


@router.message(F.text == BTN_TAGS)
async def btn_tags(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.commands import cmd_tags
    await cmd_tags(message)


@router.message(F.text == BTN_HELP)
async def btn_help(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    from bot.handlers.commands import cmd_help
    await cmd_help(message)


# ── Stateful buttons: enter FSM, wait for free text ──────────────────────

@router.message(F.text == BTN_ASK)
async def btn_ask_prompt(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_ask)
    await message.answer(
        "🧠 Напиши своє питання — я знайду відповідь у твоїй базі знань і "
        "покажу джерела.",
        reply_markup=cancel_menu(),
    )


@router.message(F.text == BTN_SEARCH)
async def btn_search_prompt(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_search)
    await message.answer(
        "🔍 Введи ключове слово або фразу (мін. 2 символи) — шукатиму в "
        "заголовках, саммарі та тегах.",
        reply_markup=cancel_menu(),
    )


@router.message(F.text == BTN_CHAT)
async def btn_chat_prompt(message: types.Message, state: FSMContext) -> None:
    await state.set_state(MenuStates.waiting_chat)
    await message.answer(
        "💬 Напиши що завгодно — це вільний AI-чат, без доступу до бази знань.",
        reply_markup=cancel_menu(),
    )


# ── FSM input handlers ───────────────────────────────────────────────────
# We explicitly exclude menu-button labels from these filters so a user who
# taps another menu button mid-input gets routed to *that* button's handler
# (registered above) instead of having the tap treated as input text.

_NOT_A_BUTTON = ~F.text.in_(MENU_BUTTONS | {BTN_CANCEL})


@router.message(MenuStates.waiting_ask, F.text, _NOT_A_BUTTON)
async def btn_ask_input(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    # Restore the main menu before the (potentially long) answer is sent so
    # the keyboard is already in place when the response comes back.
    await message.answer("🔎 Ок, шукаю…", reply_markup=main_menu(settings.webapp_url or None))
    from bot.handlers.commands import answer_ask
    await answer_ask(message, message.text or "")


@router.message(MenuStates.waiting_search, F.text, _NOT_A_BUTTON)
async def btn_search_input(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🔎 Шукаю…", reply_markup=main_menu(settings.webapp_url or None))
    from bot.handlers.extras import answer_search
    await answer_search(message, message.text or "")


@router.message(MenuStates.waiting_chat, F.text, _NOT_A_BUTTON)
async def btn_chat_input(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("💭 Думаю…", reply_markup=main_menu(settings.webapp_url or None))
    from bot.handlers.extras import answer_chat
    await answer_chat(message, message.text or "")
