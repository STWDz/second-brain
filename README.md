<div align="center">

# 🧠 Cortex

**AI-powered Telegram Knowledge Assistant**

Зберігай статті, відео, PDF і голосові в особисту базу знань.
Шукай за змістом, проходь квізи, отримуй витяги — все через Telegram.
Просто напиши — відповім як ІІ.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.13-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://aiogram.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Fly.io](https://img.shields.io/badge/Fly.io-Deployed-8B5CF6?style=for-the-badge&logo=fly.io&logoColor=white)](https://fly.io)
[![React](https://img.shields.io/badge/React-Mini_App-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://reactjs.org)
[![License](https://img.shields.io/badge/License-MIT_+_Commons_Clause-yellow?style=for-the-badge)](LICENSE)

</div>

---

## Demo

```
Ти:     https://habr.com/ru/articles/...
Бот:    ✅ Збережено!
        🎯 Головна думка: ...
        💡 3 ключових інсайти: ...
        🏷 #розробка #архітектура
        📦 4 фрагменти у базі знань
        [ 🧒 Простіше ] [ 📌 Закріпити ] [ 🗑 Видалити ]

Ти:     /ask як працює dependency injection?
Бот:    На основі твоїх нотаток: ...

Ти:     привіт, що таке docker?
Бот:    Docker — це платформа для контейнеризації...

Ти:     🎙 (голосове повідомлення)
Бот:    � Розшифровка: ...
```

---

## Що вміє

<table>
<tr>
<td width="50%">

### 📥 Прийом контенту
- 🔗 Посилання на статті → AI-витяг
- 📺 YouTube → субтитри → саммарі
- 📄 PDF-файли → витяг + обробка
- 🎙 Голосові / кружки → Whisper розшифровка
- 📸 Фото з підписом → нотатка
- 💬 Переслані повідомлення → автозбереження
- 💭 Просто текст → ІІ-чат

</td>
<td width="50%">

### 🔧 Команди
- `/ask` — RAG-пошук по базі знань
- `/search` — текстовий пошук
- `/conspect` — конспект з тексту
- `/quiz` — квіз по нотатках
- `/random` — випадкова нотатка
- `/pinned` — закріплені
- `/stats` `/export` `/tags`

</td>
</tr>
<tr>
<td>

### 🌐 Inline-режим
Набери `@BotName запит` в **будь-якому** чаті — миттєвий пошук по твоїй базі знань

</td>
<td>

### 📊 Дайджести
- **Daily** — випадкова стара нотатка (spaced repetition)
- **Weekly** — підсумки тижня + зростання бази

</td>
</tr>
</table>

---

## Архітектура

```
                          ┌──────────────────────────────┐
                          │     Telegram Bot API         │
                          └──────────┬───────────────────┘
                                     │
                          ┌──────────▼───────────────────┐
                          │   Aiogram 3 (polling/async)  │
                          └──────────┬───────────────────┘
                                     │
              ┌──────────────────────▼──────────────────────────┐
              │          7-Layer Middleware Stack                │
              │  Private → Whitelist → AntiSpam → RateLimit     │
              │  → FileSize → Sanitize → AuditLog               │
              └──────────────────────┬──────────────────────────┘
                                     │
          ┌──────────┬───────────────┼───────────────┬──────────┐
          ▼          ▼               ▼               ▼          ▼
     Commands    Content         Extras          Inline      Voice
     /ask,/help  URL,PDF,fwd    /quiz,/search   @bot query  STT
          │          │               │               │          │
          └──────────┴───────┬───────┴───────────────┴──────────┘
                             ▼
              ┌─────────────────────────────┐
              │         Services            │
              │  LLM (Groq) │ Embeddings (HF)│ Whisper │
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  PostgreSQL 16 + pgvector   │
              │  IVFFlat cosine (384d)      │
              └──────────────┬──────────────┘
                             ▼
              ┌─────────────────────────────┐
              │  aiohttp API (HMAC auth)    │
              │  ← React Mini App (Vite)    │
              └─────────────────────────────┘
```

---

## Технологічний стек

| Шар | Технологія | Навіщо |
|:-----|:-----------|:------|
| Runtime | **Python 3.12**, asyncio | Повністю асинхронна архітектура |
| Bot | **Aiogram 3.13** | Middleware pipeline, inline mode, callbacks |
| LLM | **Groq** — Llama 3.3 70B | Саммарі, теги, квізи, чат, конспекти |
| STT | **Groq Whisper** large-v3-turbo | Голос → текст |
| Embeddings | **HuggingFace** BAAI/bge-small (384d) | Векторні ембедінги для RAG |
| Database | **Neon.tech** PostgreSQL + pgvector | Дані + ANN-пошук (IVFFlat cosine) |
| ORM | **SQLAlchemy 2.0** async + Alembic | Моделі, міграції |
| Web API | **aiohttp** | REST для Mini App, HMAC-SHA256 auth |
| Frontend | **React** + Vite + TailwindCSS | Telegram Web App з нативною темою |
| Extraction | trafilatura, youtube-transcript-api, PyMuPDF | Парсинг контенту |
| Scheduler | **APScheduler** | Daily & Weekly Digest |
| Deploy | **Fly.io** + Docker | Multi-stage build, Neon DB |

> 💰 **Вартість: $0** — Groq + HuggingFace + Neon + Fly.io безкоштовні. Переключення на OpenAI — одна змінна.

---

## RAG Pipeline

```
 Збереження:
 ───────────
 Текст ──→ Chunking (800 chars, 100 overlap)
       ──→ HuggingFace API ──→ Vector(384) per chunk
       ──→ INSERT INTO chunks (content, embedding)

 Пошук:
 ──────
 /ask питання ──→ get_embedding(question)
             ──→ ORDER BY embedding <=> query LIMIT 5
             ──→ Контекст ──→ LLM + System Prompt ──→ Відповідь
```

---

## Безпека

<table>
<tr>
<td width="50%">

### 🛡 Bot Middleware (7 шарів)

| # | Middleware | Захист |
|:-:|:----------|:-------|
| 1 | **PrivateOnly** | Блок груп/каналів |
| 2 | **Whitelist** | Тільки свої user_id |
| 3 | **AntiSpam** | 3 повтори / 30 сек |
| 4 | **RateLimit** | 15 подій / хв |
| 5 | **FileSize** | Ліміт 20 МБ |
| 6 | **Sanitize** | Макс 50K символів |
| 7 | **AuditLog** | Все в audit.log |

</td>
<td width="50%">

### 🔐 Web API & Infra

- **HMAC-SHA256** валідація initData (no fallback)
- **127.0.0.1** binding — API не в інтернеті
- **30 req/min** per IP rate limit
- **Security headers**: CSP, X-Frame DENY, nosniff
- **SSRF protection**: блок private IP, DNS resolve
- **Docker**: multi-stage build, minimal image
- **Ownership check** на всіх мутаціях

</td>
</tr>
</table>

---

## Моделі даних

```sql
users                    documents                    chunks
├── id (PK)              ├── id (PK)                  ├── id (PK)
├── telegram_id (UQ)     ├── user_id (FK → users)     ├── document_id (FK → docs)
├── username             ├── title                    ├── content (TEXT)
├── first_name           ├── source_url               ├── embedding Vector(384)  ◄── IVFFlat
└── created_at           ├── source_type              └── chunk_index
                         ├── summary
                         ├── tags (JSON)
                         ├── is_pinned
                         └── created_at

 Зв'язки: users 1:N documents 1:N chunks
 Видалення: ON DELETE CASCADE (каскадне)
 Ізоляція: всі запити фільтруються по user_id
```

---

## Структура проєкту

```
cortex/
│
├── bot/
│   ├── __main__.py             Entry point + middleware registration
│   ├── config.py               Pydantic Settings (15+ params)
│   ├── middlewares.py           7-layer security stack
│   ├── prompts.py              7 AI prompt templates (🇺🇦 Ukrainian)
│   ├── scheduler.py            Daily + Weekly Digest
│   ├── webapp_api.py           REST API + HMAC + rate limiting
│   │
│   ├── db/
│   │   ├── engine.py           Async SQLAlchemy engine
│   │   ├── models.py           User, Document, Chunk (pgvector)
│   │   └── repositories.py     14 repository functions
│   │
│   ├── handlers/
│   │   ├── commands.py         /start, /ask, /tags, /help
│   │   ├── content.py          URL, PDF, photo, forwarded, text
│   │   ├── extras.py           /quiz, /search, /pinned, /stats, callbacks
│   │   ├── inline.py           Inline mode search
│   │   └── voice.py            Voice + video note STT
│   │
│   └── services/
│       ├── content.py          Extractors + SSRF protection
│       ├── openai_client.py    Multi-provider LLM/Embeddings/Whisper
│       └── rag.py              Chunking → Embedding → Retrieval
│
├── webapp/                     React Mini App (Vite + Tailwind)
├── alembic/                    Database migrations
├── docker-compose.yml          Hardened deployment
├── Dockerfile                  Python 3.12-slim
├── requirements.txt            14 dependencies
└── .env.example                Config template
```

---

## Швидкий старт

### 1. API-ключі (безкоштовно)

| Сервіс | Посилання | Що отримаєш |
|:-------|:-------|:-------------|
| Telegram | [@BotFather](https://t.me/BotFather) → `/newbot` | `BOT_TOKEN` |
| Groq | [console.groq.com](https://console.groq.com) | `GROQ_API_KEY` |
| HuggingFace | [hf.co/settings/tokens](https://huggingface.co/settings/tokens) | `HF_API_KEY` |

### 2. Запуск (локально)

```bash
git clone https://github.com/STWDZ/second-brain.git
cd second-brain

cp .env.example .env    # ← заповни 3 ключі

docker compose up -d    # PostgreSQL + бот — готово
```

### 3. Deploy на Fly.io (безкоштовно)

```bash
flyctl launch
flyctl secrets set BOT_TOKEN=... GROQ_API_KEY=... HF_API_KEY=... DATABASE_URL=...
flyctl deploy
```

### 4. Mini App (опціонально)

```bash
cd webapp && npm install && npm run build
# Deploy webapp/dist на HTTPS → вказати WEBAPP_URL в .env
```

---

## Метрики

<div align="center">

| | Кількість |
|:--|:--:|
| **Python-файлів** | 16 |
| **Рядків коду** | ~2 000 |
| **Handlers** | 17 |
| **Middleware** | 7 |
| **Repository functions** | 14 |
| **Bot commands** | 13 |
| **Content types** | 7 |
| **AI prompts** | 7 |
| **Dependencies** | 14 |

</div>

---

## Roadmap

- [x] Деплой на Fly.io + Neon.tech
- [x] ІІ-чат без команд
- [x] Конспекти /conspect
- [x] Українська локалізація
- [ ] Webhook mode (Nginx reverse proxy)
- [ ] Redis кеш ембедінгів
- [ ] TTS — голосові відповіді
- [ ] Notion / Obsidian sync
- [ ] Admin-панель

---

<div align="center">

## License

**MIT with Commons Clause** — дивитись і вчитись можна, продавати і видавати за своє не можна.

Made with 🧠 by **STWDZ**

</div>
