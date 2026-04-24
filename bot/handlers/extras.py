"""Extra handlers: /quiz, /stats, /random, /export, /chat, /search, /pinned + inline callbacks."""

import io
import json
import logging
import re
import zipfile

from aiogram import F, Router, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot.db.engine import async_session
from bot.db.repositories import (
    delete_document,
    get_document_by_id,
    get_or_create_user,
    get_pinned_documents,
    get_random_document,
    get_user_documents,
    get_user_stats,
    search_documents_text,
    toggle_pin,
)
from bot.services.formatting import send_llm_response, tg_escape
from bot.services.openai_client import free_chat, generate_quiz, make_conspect, simplify_text

logger = logging.getLogger(__name__)
router = Router()

TYPE_EMOJI = {
    "url": "🔗",
    "youtube": "📺",
    "pdf": "📄",
    "voice": "🎙",
    "text": "📝",
}


# ── /quiz ── AI generates a question from your knowledge base ──────────────

@router.message(Command("quiz"))
async def cmd_quiz(message: types.Message) -> None:
    wait_msg = await message.answer("🧩 Генерую питання з твоєї бази знань...")

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        # Get random context for the quiz
        doc = await get_random_document(session, user.id)

    if not doc or not doc.summary:
        await wait_msg.delete()
        await message.answer("📭 Мало даних для квізу. Збережи більше матеріалів!")
        return

    context = doc.summary
    quiz = await generate_quiz(context)

    if not quiz:
        await wait_msg.delete()
        await message.answer("😅 Не вдалося згенерувати питання. Спробуй ще раз!")
        return

    options = quiz["options"]
    correct = quiz["correct"]
    explanation = quiz.get("explanation", "")

    text = f"🧩 <b>Квіз по твоїх нотатках</b>\n\n❓ {tg_escape(quiz['question'])}\n\n"
    for key in ["A", "B", "C", "D"]:
        text += f"  <b>{key}.</b> {tg_escape(options.get(key, '—'))}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'🅰️' if k == 'A' else '🅱️' if k == 'B' else '©️' if k == 'C' else '🅳'}",
                    callback_data=f"quiz:{k}:{correct}:{message.from_user.id}",
                )
                for k in ["A", "B", "C", "D"]
            ]
        ]
    )

    await wait_msg.delete()
    quiz_msg = await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

    # Store explanation for callback
    # We encode it in a separate message that we can reference


@router.callback_query(F.data.startswith("quiz:"))
async def quiz_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 4:
        return
    chosen, correct, owner_id = parts[1], parts[2], parts[3]

    if str(callback.from_user.id) != owner_id:
        await callback.answer(
            "🔒 Це квіз іншого користувача. Відкрий /quiz у своєму чаті з ботом, щоб перевірити свою базу знань.",
            show_alert=True,
        )
        return

    if chosen == correct:
        await callback.answer("✅ Правильно! Мозок на місці 🧠", show_alert=True)
        result_text = f"\n\n✅ Відповідь <b>{correct}</b> — правильно!"
    else:
        await callback.answer(f"❌ Неправильно. Відповідь: {correct}", show_alert=True)
        result_text = f"\n\n❌ Ти відповів <b>{chosen}</b>, правильно: <b>{correct}</b>"

    # Update message to show result
    old_text = callback.message.text or callback.message.html_text or ""
    await callback.message.edit_text(
        old_text + result_text, parse_mode="HTML", reply_markup=None
    )


# ── /stats ── Beautiful statistics ─────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: types.Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        stats = await get_user_stats(session, user.id)

    if stats["total"] == 0:
        await message.answer("📊 У тебе поки немає статистики. Збережи щось!")
        return

    by_type = stats["by_type"]
    type_lines = []
    for t, count in sorted(by_type.items(), key=lambda x: -x[1]):
        emoji = TYPE_EMOJI.get(t, "📄")
        type_lines.append(f"  {emoji} {t}: <b>{count}</b>")

    tags_line = " ".join(stats["top_tags"][:8]) if stats["top_tags"] else "—"

    # Brain level
    total = stats["total"]
    if total < 5:
        level = "🌱 Паросток"
    elif total < 20:
        level = "🌿 Зростаючий мозок"
    elif total < 50:
        level = "🧠 Розумний мозок"
    elif total < 100:
        level = "🔬 Мега-мозок"
    else:
        level = "🏆 Геній"

    bar_fill = min(total, 100)
    bar = "█" * (bar_fill // 5) + "░" * (20 - bar_fill // 5)

    first = stats["first_save"].strftime("%d.%m.%Y") if stats["first_save"] else "—"

    text = (
        f"📊 <b>Статистика твого Cortex</b>\n\n"
        f"🎖 Рівень: <b>{level}</b>\n"
        f"<code>[{bar}]</code> {total}/100\n\n"
        f"📚 Всього матеріалів: <b>{total}</b>\n"
        + "\n".join(type_lines) + "\n\n"
        f"🧩 Фрагментів у памʼяті: <b>{stats['total_chunks']}</b>\n"
        f"🏷 Унікальних тегів: <b>{stats['tags_count']}</b>\n"
        f"🔝 Топ теги: {tags_line}\n\n"
        f"📅 З нами з: {first}"
    )
    await message.answer(text, parse_mode="HTML")


# ── /random ── Random note for inspiration ─────────────────────────────────

@router.message(Command("random"))
async def cmd_random(message: types.Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        doc = await get_random_document(session, user.id)

    if not doc:
        await message.answer("📭 База знань порожня. Збережи щось!")
        return

    tags = ""
    if doc.tags:
        try:
            tags = " ".join(json.loads(doc.tags))
        except (json.JSONDecodeError, TypeError):
            pass

    emoji = TYPE_EMOJI.get(doc.source_type, "📄")
    text = f"🎲 <b>Випадкова нотатка</b>\n\n{emoji} <b>{tg_escape(doc.title or 'Без назви')}</b>\n"
    if doc.source_url:
        text += f"🔗 {tg_escape(doc.source_url)}\n"
    if doc.summary:
        text += f"\n{tg_escape(doc.summary)}\n"
    if tags:
        text += f"\n🏷 {tg_escape(tags)}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎲 Ще", callback_data=f"random:{message.from_user.id}"),
                InlineKeyboardButton(text="🗑 Видалити", callback_data=f"del:{doc.id}:{message.from_user.id}"),
            ]
        ]
    )
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(F.data.startswith("random:"))
async def random_callback(callback: CallbackQuery) -> None:
    owner_id = callback.data.split(":")[1]
    if str(callback.from_user.id) != owner_id:
        await callback.answer(
            "🔒 Це нотатка іншого користувача. Надішли /random у своєму чаті з ботом.",
            show_alert=True,
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        doc = await get_random_document(session, user.id)

    if not doc:
        await callback.answer("Більше нічого немає!", show_alert=True)
        return

    tags = ""
    if doc.tags:
        try:
            tags = " ".join(json.loads(doc.tags))
        except (json.JSONDecodeError, TypeError):
            pass

    emoji = TYPE_EMOJI.get(doc.source_type, "📄")
    text = f"🎲 <b>Випадкова нотатка</b>\n\n{emoji} <b>{tg_escape(doc.title or 'Без назви')}</b>\n"
    if doc.source_url:
        text += f"🔗 {tg_escape(doc.source_url)}\n"
    if doc.summary:
        text += f"\n{tg_escape(doc.summary)}\n"
    if tags:
        text += f"\n🏷 {tg_escape(tags)}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎲 Ще", callback_data=f"random:{callback.from_user.id}"),
                InlineKeyboardButton(text="🗑 Видалити", callback_data=f"del:{doc.id}:{callback.from_user.id}"),
            ]
        ]
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


# ── Delete callback ────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("del:"))
async def delete_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    doc_id, owner_id = int(parts[1]), parts[2]

    if str(callback.from_user.id) != owner_id:
        await callback.answer(
            "🔒 Це нотатка іншого користувача — ти не можеш її видалити.",
            show_alert=True,
        )
        return

    # Defence in depth: repository layer also scopes by user_id.
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        doc = await get_document_by_id(session, doc_id, user_id=user.id)
        if not doc:
            await callback.answer("Документ не знайдено або він не твій.", show_alert=True)
            return

        deleted = await delete_document(session, doc_id, user_id=user.id)
        await session.commit()

    if deleted:
        await callback.message.edit_text("🗑 <b>Видалено з бази знань.</b>", parse_mode="HTML")
        await callback.answer("Видалено!")
    else:
        await callback.answer("Вже видалено", show_alert=True)


# ── Simplify callback ─────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("simplify:"))
async def simplify_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    doc_id, owner_id = int(parts[1]), parts[2]

    if str(callback.from_user.id) != owner_id:
        await callback.answer(
            "🔒 Це документ іншого користувача — ти не можеш його спростити.",
            show_alert=True,
        )
        return

    await callback.answer("🔄 Спрощую...")

    # Verify ownership at the repository layer
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        doc = await get_document_by_id(session, doc_id, user_id=user.id)

    if not doc or not doc.summary:
        await callback.message.answer("Нема що спрощувати.")
        return

    simple = await simplify_text(doc.summary)
    await send_llm_response(
        callback.message, f"🧒 <b>Пояснюю простіше:</b>\n\n{simple}"
    )


# ── /export ── Export all notes as Markdown ────────────────────────────────

@router.message(Command("export"))
async def cmd_export(message: types.Message) -> None:
    wait_msg = await message.answer("📦 Готую експорт...")

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        docs = await get_user_documents(session, user.id, limit=500)

    if not docs:
        await wait_msg.delete()
        await message.answer("📭 Нема що експортувати.")
        return

    md_lines = ["# 🧠 Cortex — Експорт\n"]
    for doc in docs:
        emoji = TYPE_EMOJI.get(doc.source_type, "📄")
        date_str = doc.created_at.strftime("%d.%m.%Y %H:%M") if doc.created_at else ""
        md_lines.append(f"\n---\n\n## {emoji} {doc.title or 'Без назви'}\n")
        md_lines.append(f"📅 {date_str}\n")
        if doc.source_url:
            md_lines.append(f"🔗 [{doc.source_url}]({doc.source_url})\n")
        if doc.tags:
            try:
                tags = json.loads(doc.tags)
                md_lines.append(f"🏷 {' '.join(tags)}\n")
            except (json.JSONDecodeError, TypeError):
                pass
        if doc.summary:
            md_lines.append(f"\n{doc.summary}\n")

    content = "\n".join(md_lines)
    file = BufferedInputFile(
        content.encode("utf-8"), filename="second_brain_export.md"
    )

    await wait_msg.delete()
    await message.answer_document(file, caption=f"📦 Експорт: {len(docs)} нотаток")


# ── /export_obsidian ── One .md per note, zipped (Obsidian-friendly) ───────


_SLUG_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def _slugify(text: str, fallback: str) -> str:
    """Make a filesystem-safe filename from a title."""
    text = _SLUG_RE.sub("", text).strip()
    text = re.sub(r"\s+", "-", text)
    return (text[:80] or fallback).strip("-") or fallback


@router.message(Command("export_obsidian"))
async def cmd_export_obsidian(message: types.Message) -> None:
    wait_msg = await message.answer("📦 Готую Obsidian-vault ZIP...")

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        docs = await get_user_documents(session, user.id, limit=1000)

    if not docs:
        await wait_msg.delete()
        await message.answer("📭 Нема що експортувати.")
        return

    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in docs:
            title = doc.title or "Без назви"
            base = _slugify(title, fallback=f"note-{doc.id}")
            # Deduplicate file names across the vault
            name = base
            counter = 2
            while name in used_names:
                name = f"{base}-{counter}"
                counter += 1
            used_names.add(name)

            tags_yaml = ""
            try:
                tag_list = json.loads(doc.tags) if doc.tags else []
            except (ValueError, TypeError):
                tag_list = []
            if tag_list:
                clean = [str(t).lstrip("#") for t in tag_list if t]
                tags_yaml = "tags:\n" + "\n".join(f"  - {t}" for t in clean) + "\n"

            created = (
                doc.created_at.isoformat() if doc.created_at else ""
            )
            safe_title = title.replace('"', "'")

            frontmatter = (
                "---\n"
                f'title: "{safe_title}"\n'
                f"created: {created}\n"
                f"source_type: {doc.source_type}\n"
                + (f"source: {doc.source_url}\n" if doc.source_url else "")
                + tags_yaml
                + ("pinned: true\n" if doc.is_pinned else "")
                + "---\n\n"
            )
            body = doc.summary or ""
            zf.writestr(f"{name}.md", frontmatter + body)

        # Small README inside the vault
        readme = (
            "# Cortex → Obsidian\n\n"
            "Це експорт твоєї бази знань з бота Cortex.\n"
            "Просто розпакуй цей ZIP у свій Obsidian Vault — кожна нотатка стане окремим файлом, "
            "з YAML frontmatter (теги, джерело, дата).\n"
        )
        zf.writestr("README.md", readme)

    buf.seek(0)
    file = BufferedInputFile(buf.read(), filename="cortex-obsidian-vault.zip")

    await wait_msg.delete()
    await message.answer_document(
        file, caption=f"📦 Obsidian vault: {len(docs)} нотаток у ZIP"
    )


# ── /chat ── Free chat with AI ────────────────────────────────────────────

@router.message(Command("chat"))
async def cmd_chat(message: types.Message, command: CommandObject) -> None:
    text = command.args
    if not text:
        await message.answer(
            "💬 Використовуй: /chat <i>будь-що</i>\n\n"
            "Вільний чат з ІІ — без бази знань, просто розмова.",
            parse_mode="HTML",
        )
        return

    wait_msg = await message.answer("💭 Думаю...")
    answer = await free_chat(text)
    await wait_msg.delete()
    await send_llm_response(message, answer)


# ── /conspect ── Generate structured conspect from text ───────────────────

@router.message(Command("conspect"))
async def cmd_conspect(message: types.Message, command: CommandObject) -> None:
    text = command.args
    # Also support reply to a message
    if not text and message.reply_to_message and message.reply_to_message.text:
        text = message.reply_to_message.text
    if not text or len(text.strip()) < 30:
        await message.answer(
            "📋 Використовуй: /conspect <i>текст</i>\n\n"
            "Або відповідж на повідомлення командою /conspect — зроблю конспект.\n"
            "Мінімум 30 символів.",
            parse_mode="HTML",
        )
        return

    wait_msg = await message.answer("📋 Роблю конспект...")
    try:
        result = await make_conspect(text)
        await wait_msg.delete()
        await send_llm_response(message, result)
    except Exception as e:
        logger.exception("Conspect error: %s", e)
        await wait_msg.delete()
        await message.answer("❌ Не вдалося зробити конспект. Спробуй ще раз.")


# ── /search ── Text search across notes ────────────────────────────────────

@router.message(Command("search"))
async def cmd_search(message: types.Message, command: CommandObject) -> None:
    query = command.args
    if not query or len(query.strip()) < 2:
        await message.answer(
            "🔍 Використовуй: /search <i>ключове слово</i>", parse_mode="HTML"
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        docs = await search_documents_text(session, user.id, query.strip())

    if not docs:
        await message.answer(f"🔍 За запитом «{query}» нічого не знайдено.")
        return

    lines = [f"🔍 <b>Результати за «{tg_escape(query)}»:</b>\n"]
    for i, doc in enumerate(docs[:10], 1):
        emoji = TYPE_EMOJI.get(doc.source_type, "📄")
        pin = "📌 " if doc.is_pinned else ""
        title = doc.title or "Без назви"
        if len(title) > 60:
            title = title[:57] + "..."
        lines.append(f"{i}. {pin}{emoji} <b>{tg_escape(title)}</b>")
        if doc.summary:
            preview = doc.summary[:100].replace("\n", " ")
            lines.append(f"   <i>{tg_escape(preview)}...</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── /pinned ── Show pinned notes ───────────────────────────────────────────

@router.message(Command("pinned"))
async def cmd_pinned(message: types.Message) -> None:
    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=message.from_user.id)
        docs = await get_pinned_documents(session, user.id)

    if not docs:
        await message.answer("📌 Немає закріплених нотаток. Закріпи через кнопку після збереження!")
        return

    lines = ["📌 <b>Закріплені нотатки:</b>\n"]
    for i, doc in enumerate(docs, 1):
        emoji = TYPE_EMOJI.get(doc.source_type, "📄")
        title = doc.title or "Без назви"
        lines.append(f"{i}. {emoji} <b>{tg_escape(title)}</b>")
        if doc.summary:
            preview = doc.summary[:80].replace("\n", " ")
            lines.append(f"   <i>{tg_escape(preview)}...</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ── Pin callback ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pin:"))
async def pin_callback(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    doc_id, owner_id = int(parts[1]), parts[2]

    if str(callback.from_user.id) != owner_id:
        await callback.answer(
            "🔒 Це документ іншого користувача — ти не можеш його закріпити.",
            show_alert=True,
        )
        return

    async with async_session() as session:
        user = await get_or_create_user(session, telegram_id=callback.from_user.id)
        doc = await get_document_by_id(session, doc_id, user_id=user.id)
        if not doc:
            await callback.answer("Документ не знайдено.", show_alert=True)
            return

        is_pinned = await toggle_pin(session, doc_id, user_id=user.id)
        await session.commit()

    if is_pinned:
        await callback.answer("📌 Закріплено!", show_alert=False)
    else:
        await callback.answer("Відкріплено", show_alert=False)
