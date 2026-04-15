<div align="center">

# 🧠 Second Brain

**AI-powered Telegram Knowledge Assistant**

Сохраняй статьи, видео, PDF и голосовые в личную базу знаний.
Ищи по смыслу, проходи квизы, получай выжимки — всё через Telegram.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.13-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://aiogram.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16_+_pgvector-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)
[![React](https://img.shields.io/badge/React-Mini_App-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://reactjs.org)
[![License](https://img.shields.io/badge/License-MIT_+_Commons_Clause-yellow?style=for-the-badge)](LICENSE)

</div>

---

## Demo

```
Ты:     https://habr.com/ru/articles/...
Бот:    ✅ Сохранено!
        🎯 Главная мысль: ...
        💡 3 ключевых инсайта: ...
        🏷 #разработка #архитектура
        📦 4 фрагмента в базе знаний
        [ 🧒 Проще ] [ 📌 Закрепить ] [ 🗑 Удалить ]

Ты:     /ask как работает dependency injection?
Бот:    На основе твоих заметок: ...

Ты:     @BotName docker volumes     ← из любого чата
Бот:    🔗 Docker: Persistent Storage — ...
```

---

## Что умеет

<table>
<tr>
<td width="50%">

### 📥 Приём контента
- 🔗 Ссылки на статьи → AI-выжимка
- 📺 YouTube → субтитры → саммари
- 📄 PDF-файлы → извлечение + обработка
- 🎙 Голосовые / кружки → Whisper STT
- 📸 Фото с подписью → заметка
- 💬 Пересланные сообщения → автосохранение

</td>
<td width="50%">

### 🔧 Команды
- `/ask` — RAG-поиск по базе знаний
- `/search` — текстовый поиск
- `/chat` — свободный чат с AI
- `/quiz` — квиз по заметкам
- `/random` — случайная заметка
- `/pinned` — закреплённые
- `/stats` `/export` `/tags`

</td>
</tr>
<tr>
<td>

### 🌐 Inline-режим
Набери `@BotName запрос` в **любом** чате — мгновенный поиск по твоей базе знаний

</td>
<td>

### 📊 Дайджесты
- **Daily** — случайная старая заметка (spaced repetition)
- **Weekly** — итоги недели + рост базы

</td>
</tr>
</table>

---

## Архитектура

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

## Стек технологий

| Слой | Технология | Зачем |
|:-----|:-----------|:------|
| Runtime | **Python 3.12**, asyncio | Полностью асинхронная архитектура |
| Bot | **Aiogram 3.13** | Middleware pipeline, inline mode, callbacks |
| LLM | **Groq** — Llama 3.3 70B | Суммаризация, теги, квизы, чат |
| STT | **Groq Whisper** large-v3-turbo | Голос → текст |
| Embeddings | **HuggingFace** BAAI/bge-small (384d) | Векторные эмбеддинги для RAG |
| Database | **PostgreSQL 16** + pgvector | Данные + ANN-поиск (IVFFlat cosine) |
| ORM | **SQLAlchemy 2.0** async + Alembic | Модели, миграции |
| Web API | **aiohttp** | REST для Mini App, HMAC-SHA256 auth |
| Frontend | **React** + Vite + TailwindCSS | Telegram Web App с нативной темой |
| Extraction | trafilatura, youtube-transcript-api, PyMuPDF | Парсинг контента |
| Scheduler | **APScheduler** | Daily & Weekly Digest |
| Deploy | **Docker Compose** | Hardened: read-only FS, healthcheck, no ports |

> 💰 **Стоимость: $0** — Groq + HuggingFace бесплатны. Переключение на OpenAI — одна переменная.

---

## RAG Pipeline

```
 Сохранение:
 ───────────
 Текст ──→ Chunking (800 chars, 100 overlap)
       ──→ HuggingFace API ──→ Vector(384) per chunk
       ──→ INSERT INTO chunks (content, embedding)

 Поиск:
 ──────
 /ask вопрос ──→ get_embedding(question)
             ──→ ORDER BY embedding <=> query LIMIT 5
             ──→ Контекст ──→ LLM + System Prompt ──→ Ответ
```

---

## Безопасность

<table>
<tr>
<td width="50%">

### 🛡 Bot Middleware (7 слоёв)

| # | Middleware | Защита |
|:-:|:----------|:-------|
| 1 | **PrivateOnly** | Блок групп/каналов |
| 2 | **Whitelist** | Только свои user_id |
| 3 | **AntiSpam** | 3 повтора / 30 сек |
| 4 | **RateLimit** | 15 событий / мин |
| 5 | **FileSize** | Лимит 20 МБ |
| 6 | **Sanitize** | Макс 50K символов |
| 7 | **AuditLog** | Всё в audit.log |

</td>
<td width="50%">

### 🔐 Web API & Infra

- **HMAC-SHA256** валидация initData (no fallback)
- **127.0.0.1** binding — API не в интернете
- **30 req/min** per IP rate limit
- **Security headers**: CSP, X-Frame DENY, nosniff
- **SSRF protection**: блок private IP, DNS resolve
- **Docker**: no ports, read-only FS, healthcheck
- **Ownership check** на всех мутациях

</td>
</tr>
</table>

---

## Модели данных

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

 Связи: users 1:N documents 1:N chunks
 Удаление: ON DELETE CASCADE (каскадное)
 Изоляция: все запросы фильтруются по user_id
```

---

## Структура проекта

```
second-brain/
│
├── bot/
│   ├── __main__.py             Entry point + middleware registration
│   ├── config.py               Pydantic Settings (15+ params)
│   ├── middlewares.py           7-layer security stack
│   ├── prompts.py              6 AI prompt templates
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

## Быстрый старт

### 1. API-ключи (бесплатно)

| Сервис | Ссылка | Что получишь |
|:-------|:-------|:-------------|
| Telegram | [@BotFather](https://t.me/BotFather) → `/newbot` | `BOT_TOKEN` |
| Groq | [console.groq.com](https://console.groq.com) | `GROQ_API_KEY` |
| HuggingFace | [hf.co/settings/tokens](https://huggingface.co/settings/tokens) | `HF_API_KEY` |

### 2. Запуск

```bash
git clone https://github.com/STWDZ/second-brain.git
cd second-brain

cp .env.example .env    # ← заполни 3 ключа

docker compose up -d    # PostgreSQL + бот — готово
```

### 3. Mini App (опционально)

```bash
cd webapp && npm install && npm run build
# Deploy webapp/dist на HTTPS → указать WEBAPP_URL в .env
```

---

## Метрики

<div align="center">

| | Количество |
|:--|:--:|
| **Python-файлов** | 16 |
| **Строк кода** | ~1 800 |
| **Handlers** | 16 |
| **Middleware** | 7 |
| **Repository functions** | 14 |
| **Bot commands** | 12 |
| **Content types** | 6 |
| **AI prompts** | 6 |
| **Dependencies** | 14 |

</div>

---

## Roadmap

- [ ] Webhook mode (Nginx reverse proxy)
- [ ] PostgreSQL FTS с GIN-индексом
- [ ] Redis кэш эмбеддингов
- [ ] TTS — голосовые ответы
- [ ] Notion / Obsidian sync
- [ ] Admin-панель
- [ ] Prometheus + Grafana мониторинг

---

<div align="center">

## License

**MIT with Commons Clause** — смотреть и учиться можно, продавать и выдавать за своё нельзя.

Made with 🧠 by **STWDZ**

</div>
