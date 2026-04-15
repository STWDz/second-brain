import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_URL || ''

const SOURCE_ICONS = {
  url: '🔗',
  youtube: '📺',
  pdf: '📄',
  voice: '🎙',
  text: '📝',
}

function App() {
  const [documents, setDocuments] = useState([])
  const [tags, setTags] = useState([])
  const [activeTag, setActiveTag] = useState(null)
  const [loading, setLoading] = useState(true)
  const [expandedId, setExpandedId] = useState(null)

  const tg = window.Telegram?.WebApp
  const telegramId = tg?.initDataUnsafe?.user?.id

  useEffect(() => {
    if (tg) {
      tg.ready()
      tg.expand()
    }
  }, [])

  useEffect(() => {
    if (!telegramId) return
    fetchTags()
    fetchDocuments()
  }, [telegramId])

  useEffect(() => {
    fetchDocuments()
  }, [activeTag])

  async function fetchDocuments() {
    if (!telegramId) return
    setLoading(true)
    try {
      let url = `${API_BASE}/api/documents?telegram_id=${telegramId}`
      if (activeTag) url += `&tag=${encodeURIComponent(activeTag)}`
      const res = await fetch(url)
      const data = await res.json()
      setDocuments(data)
    } catch (err) {
      console.error('Failed to fetch documents:', err)
    }
    setLoading(false)
  }

  async function fetchTags() {
    try {
      const res = await fetch(`${API_BASE}/api/tags?telegram_id=${telegramId}`)
      const data = await res.json()
      setTags(data)
    } catch (err) {
      console.error('Failed to fetch tags:', err)
    }
  }

  function formatDate(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
  }

  if (!telegramId) {
    return (
      <div className="flex items-center justify-center h-screen p-4">
        <p className="text-tg-hint text-center">
          Откройте это приложение через Telegram-бот 🧠
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-tg-bg pb-6">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-tg-bg/80 backdrop-blur-md border-b border-tg-secondary px-4 py-3">
        <h1 className="text-lg font-bold text-tg-text">🧠 Second Brain</h1>
        <p className="text-xs text-tg-hint mt-0.5">
          {documents.length} материал{documents.length === 1 ? '' : 'ов'}
        </p>
      </header>

      {/* Tags filter */}
      {tags.length > 0 && (
        <div className="flex gap-2 overflow-x-auto px-4 py-3 no-scrollbar">
          <button
            onClick={() => setActiveTag(null)}
            className={`shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors ${
              activeTag === null
                ? 'bg-tg-button text-tg-button-text'
                : 'bg-tg-secondary text-tg-hint'
            }`}
          >
            Все
          </button>
          {tags.map((tag) => (
            <button
              key={tag}
              onClick={() => setActiveTag(activeTag === tag ? null : tag)}
              className={`shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                activeTag === tag
                  ? 'bg-tg-button text-tg-button-text'
                  : 'bg-tg-secondary text-tg-hint'
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-tg-button border-t-transparent" />
        </div>
      ) : documents.length === 0 ? (
        <div className="text-center py-20 px-4">
          <p className="text-4xl mb-3">📭</p>
          <p className="text-tg-hint">Пока ничего не сохранено</p>
          <p className="text-tg-hint text-sm mt-1">
            Отправьте ссылку, PDF или голосовое в бот
          </p>
        </div>
      ) : (
        <div className="px-4 space-y-3 mt-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="bg-tg-secondary rounded-xl p-4 transition-all"
              onClick={() => setExpandedId(expandedId === doc.id ? null : doc.id)}
            >
              {/* Card header */}
              <div className="flex items-start gap-3">
                <span className="text-2xl shrink-0">
                  {SOURCE_ICONS[doc.source_type] || '📄'}
                </span>
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-sm text-tg-text leading-tight line-clamp-2">
                    {doc.title || 'Без названия'}
                  </h3>
                  <p className="text-xs text-tg-hint mt-1">{formatDate(doc.created_at)}</p>
                </div>
              </div>

              {/* Tags */}
              {doc.tags && doc.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2.5">
                  {doc.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 bg-tg-bg rounded-md text-[10px] text-tg-link font-medium"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Expanded summary */}
              {expandedId === doc.id && doc.summary && (
                <div className="mt-3 pt-3 border-t border-tg-bg">
                  <p className="text-xs text-tg-text whitespace-pre-wrap leading-relaxed">
                    {doc.summary}
                  </p>
                  {doc.source_url && (
                    <a
                      href={doc.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-block mt-2 text-xs text-tg-link underline"
                      onClick={(e) => e.stopPropagation()}
                    >
                      Открыть источник →
                    </a>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default App
