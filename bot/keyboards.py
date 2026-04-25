"""Reply keyboards for Cortex.

We expose the *labels* as module-level constants and reuse them both when
building the keyboard and when matching `F.text == BTN_X` in handlers.
This way, a label rename only needs one edit.

Rare/advanced actions (/conspect, /export, /notion_*, admin) stay as slash
commands — the menu keeps the UI calm instead of drowning the user.
"""

from __future__ import annotations

from aiogram.types import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

# ── Button labels (single source of truth) ────────────────────────────────
BTN_ASK = "🧠 Спитати базу"
BTN_SEARCH = "🔍 Пошук"
BTN_RANDOM = "🎲 Випадкова"
BTN_QUIZ = "🧩 Квіз"
BTN_PINNED = "📌 Закріплені"
BTN_STATS = "📊 Статистика"
BTN_TAGS = "🏷 Теги"
BTN_CHAT = "💬 AI-чат"
BTN_HELP = "❓ Довідка"
BTN_WEBAPP = "📚 Моя база"
BTN_CANCEL = "❌ Скасувати"

#: All menu buttons that trigger an action — used by FSM handlers to detect
#: that a user pressed a menu button while in an input state and should be
#: routed to the button handler instead of treated as input.
MENU_BUTTONS: set[str] = {
    BTN_ASK, BTN_SEARCH, BTN_RANDOM, BTN_QUIZ,
    BTN_PINNED, BTN_STATS, BTN_TAGS, BTN_CHAT, BTN_HELP,
}


def main_menu(webapp_url: str | None = None) -> ReplyKeyboardMarkup:
    """Persistent 2-column menu. 5 rows on mobile = fits above the typing box.

    If ``webapp_url`` is set the last row gets a native WebApp launcher,
    otherwise Довідка takes the full width.
    """
    rows = [
        [KeyboardButton(text=BTN_ASK), KeyboardButton(text=BTN_SEARCH)],
        [KeyboardButton(text=BTN_RANDOM), KeyboardButton(text=BTN_QUIZ)],
        [KeyboardButton(text=BTN_PINNED), KeyboardButton(text=BTN_STATS)],
        [KeyboardButton(text=BTN_TAGS), KeyboardButton(text=BTN_CHAT)],
    ]
    last = [KeyboardButton(text=BTN_HELP)]
    if webapp_url:
        last.append(KeyboardButton(text=BTN_WEBAPP, web_app=WebAppInfo(url=webapp_url)))
    rows.append(last)

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Обери дію або надішли лінк / PDF / голосове…",
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    """Shown while waiting for FSM input — single ❌ Скасувати button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Введи текст або натисни ❌ Скасувати",
    )


def hide_menu() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()
