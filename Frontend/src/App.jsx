import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import './App.css'

const API_URL        = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'
const STORAGE_KEY    = 'ollama_conversations'
const MAX_STORED_CONVOS = 50
const MAX_INPUT_CHARS   = 2000

// ── Personalities ─────────────────────────────────────────────────────────────
const PERSONALITIES = {
    helpful:      'You are a helpful assistant. Answer clearly and accurately.',
    creative:     'You are a creative assistant. Give imaginative and original answers.',
    technical:    'You are a technical assistant. Give precise, detailed explanations.',
    casual:       'You are a casual, friendly assistant. Use simple, natural language.',
    professional: 'You are a professional assistant. Give structured, concise answers.',
}


// ── Icons ─────────────────────────────────────────────────────────────────────
const PlusIcon = () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
)
const SendIcon = () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
)
const StopIcon = () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
        <rect x="4" y="4" width="16" height="16" rx="3" />
    </svg>
)
const UserIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
    </svg>
)
const BotIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
        <path d="M12 2l2.4 7.4H22l-6.2 4.5 2.4 7.4L12 17l-6.2 4.3 2.4-7.4L2 9.4h7.6z"/>
    </svg>
)
const SearchIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
)
const TrashIcon = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
        <path d="M10 11v6M14 11v6" />
        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
)
const CopyIcon = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
)
const CheckIcon = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
    </svg>
)
const SettingsIcon = () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
)
const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
)
const MenuIcon = () => (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
    </svg>
)
const ChatIcon = () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
    </svg>
)
const RetryIcon = () => (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 .49-3.96" />
    </svg>
)
const AttachIcon = () => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
    </svg>
)
const ExportIcon = () => (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="7 10 12 15 17 10" />
        <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
)
const ChevronDownIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="6 9 12 15 18 9" />
    </svg>
)

// ── Helpers ───────────────────────────────────────────────────────────────────
function genId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2)
}

function newConversation() {
    return { id: genId(), title: 'New chat', messages: [], createdAt: Date.now(), updatedAt: Date.now() }
}

function loadConversations() {
    try {
        const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
        if (Array.isArray(raw) && raw.length > 0) return raw
    } catch { /* */ }
    return [newConversation()]
}

function saveConversations(convos) {
    const capped = convos.slice(0, MAX_STORED_CONVOS)
    const clean = capped.map(c => ({
        ...c,
        messages: c.messages.filter(m => !m.loading && !m.streaming),
    }))
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(clean))
    } catch {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(clean.slice(0, Math.floor(MAX_STORED_CONVOS / 2))))
        } catch { /* quota exceeded — give up */ }
    }
}

function groupByDate(convos) {
    const now = Date.now(), DAY = 86_400_000
    const groups = { Today: [], Yesterday: [], 'Past 7 days': [], Older: [] }
    for (const c of convos) {
        const age = now - c.updatedAt
        if (age < DAY)           groups.Today.push(c)
        else if (age < 2 * DAY)  groups.Yesterday.push(c)
        else if (age < 7 * DAY)  groups['Past 7 days'].push(c)
        else                     groups.Older.push(c)
    }
    return groups
}

function formatTs(ts) {
    if (!ts) return ''
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// ── Code block ────────────────────────────────────────────────────────────────
function CodeBlock({ inline, className, children, ...props }) {
    const [copied, setCopied] = useState(false)
    const code = String(children).replace(/\n$/, '')
    const lang = /language-(\w+)/.exec(className || '')?.[1] ?? 'text'

    if (inline) return <code className="inline-code" {...props}>{children}</code>

    return (
        <div className="code-block">
            <div className="code-block__header">
                <span className="code-block__lang">{lang}</span>
                <button
                    className="code-block__copy"
                    aria-label={copied ? 'Copied to clipboard' : 'Copy code'}
                    onClick={() => {
                        navigator.clipboard.writeText(code)
                        setCopied(true)
                        setTimeout(() => setCopied(false), 2000)
                    }}
                >
                    {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
                </button>
            </div>
            <SyntaxHighlighter style={oneDark} language={lang} PreTag="div"
                customStyle={{ margin: 0, borderRadius: '0 0 10px 10px', fontSize: '13px', background: '#1a1a1a' }} {...props}>
                {code}
            </SyntaxHighlighter>
        </div>
    )
}

const MD_COMPONENTS = {
    code: CodeBlock,
    a: ({ href, children }) => (
        <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    ),
}

// ── Suggested prompts ─────────────────────────────────────────────────────────
const SUGGESTED_PROMPTS = [
    { label: 'Browse shoes',       text: 'Show me the best running shoes available' },
    { label: 'Budget electronics', text: 'Show me headphones under ₹2000'           },
    { label: 'Top rated',          text: 'What are the highest rated products?'      },
    { label: 'Recommendations',    text: 'Recommend something for a home workout'    },
]

function SuggestedPrompts({ onSelect }) {
    return (
        <div className="suggested-prompts">
            {SUGGESTED_PROMPTS.map((p) => (
                <button key={p.label} className="prompt-card" onClick={() => onSelect(p.text)}>
                    <span className="prompt-card__label">{p.label}</span>
                    <span className="prompt-card__text">{p.text}</span>
                </button>
            ))}
        </div>
    )
}

// ── Typing indicator ──────────────────────────────────────────────────────────
function TypingDots() {
    return <span className="typing-dots" aria-hidden="true"><span /><span /><span /></span>
}

// ── Message ───────────────────────────────────────────────────────────────────
const TOOL_META = {
    search_products:    { label: 'Searching catalog',       icon: '🔍' },
    get_recommendations:{ label: 'Finding similar products', icon: '✦'  },
    fetch_from_fynd:    { label: 'Fetching from Fynd',      icon: '🛒' },
}

function ToolPills({ toolCalls }) {
    if (!toolCalls?.length) return null
    return (
        <div className="tool-pills" aria-label="Tool activity">
            {toolCalls.map((tc, i) => {
                const meta = TOOL_META[tc.tool] || { label: tc.tool, icon: '⚙' }
                const status = tc.found === null ? 'running' : tc.found ? 'found' : 'miss'
                const statusLabel = tc.found === null ? 'running' : tc.found ? 'results found' : 'no results'
                return (
                    <span
                        key={i}
                        className={`tool-pill tool-pill--${status}`}
                        aria-label={`${meta.label}: ${statusLabel}`}
                    >
                        <span className="tool-pill__icon" aria-hidden="true">{meta.icon}</span>
                        <span className="tool-pill__label">{meta.label}</span>
                        {tc.found === null && <span className="tool-pill__spinner" aria-hidden="true" />}
                        {tc.found === true  && <span className="tool-pill__dot tool-pill__dot--found" aria-hidden="true" />}
                        {tc.found === false && <span className="tool-pill__dot tool-pill__dot--miss" aria-hidden="true" />}
                    </span>
                )
            })}
        </div>
    )
}

function Message({ msg, onRetry }) {
    const [copied, setCopied] = useState(false)
    const isError = Boolean(msg.error && !msg.loading && !msg.streaming)
    const isDone  = msg.role === 'assistant' && !msg.loading && !msg.streaming && msg.content
    const isUser  = msg.role === 'user'

    return (
        <div className={`message message--${msg.role}`}>
            <div className="message__avatar" aria-hidden="true">
                {isUser ? <UserIcon /> : <BotIcon />}
            </div>

            <div className="message__body">
                <div className="message__header">
                    <span className="message__sender">{isUser ? 'You' : 'Fynd AI'}</span>
                    {msg.ts && <span className="message__ts">{formatTs(msg.ts)}</span>}
                </div>

                {!isUser && <ToolPills toolCalls={msg.toolCalls} />}

                <div className={`message__bubble${isError ? ' message__bubble--error' : ''}`}>
                    {msg.loading ? (
                        <>
                            <TypingDots />
                            <span className="sr-only">Fynd AI is typing…</span>
                        </>
                    ) : (
                        isUser ? (
                            <span className="message__text">{msg.content}</span>
                        ) : (
                            <div className="message__markdown">
                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                    {msg.content}
                                </ReactMarkdown>
                                {msg.streaming && <span className="stream-cursor" aria-hidden="true" />}
                            </div>
                        )
                    )}
                </div>

                {isDone && (
                    <div className="message__actions">
                        {isError ? (
                            onRetry && (
                                <button className="action-btn action-btn--retry" onClick={onRetry}>
                                    <RetryIcon /> Retry
                                </button>
                            )
                        ) : (
                            <button
                                className="action-btn"
                                aria-label={copied ? 'Copied to clipboard' : 'Copy response'}
                                onClick={() => {
                                    navigator.clipboard.writeText(msg.content)
                                    setCopied(true)
                                    setTimeout(() => setCopied(false), 2000)
                                }}
                            >
                                {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
                            </button>
                        )}
                        {msg.ttft_ms && !isError && (
                            <span className="ttft-badge" title="Time to first token">{msg.ttft_ms}ms</span>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}


// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar({ convos, activeId, onSelect, onNew, onDelete, collapsed, onToggle, search, onSearch }) {
    const [confirmDeleteId, setConfirmDeleteId] = useState(null)
    const groups = groupByDate([...convos].sort((a, b) => b.updatedAt - a.updatedAt))

    const filtered = search.trim()
        ? (() => {
            const q = search.toLowerCase()
            return convos.filter(c =>
                c.title.toLowerCase().includes(q) ||
                c.messages.some(m => m.content?.toLowerCase().includes(q))
            )
          })()
        : null

    const renderItem = (c) => (
        <div
            key={c.id}
            className={`sidebar__item ${c.id === activeId ? 'sidebar__item--active' : ''} ${confirmDeleteId === c.id ? 'sidebar__item--confirming' : ''}`}
            onClick={() => { if (confirmDeleteId !== c.id) { onSelect(c.id); setConfirmDeleteId(null) } }}
            role="button"
            tabIndex={0}
            aria-label={c.title}
            aria-current={c.id === activeId ? 'true' : undefined}
            onKeyDown={e => {
                if ((e.key === 'Enter' || e.key === ' ') && confirmDeleteId !== c.id) {
                    e.preventDefault()
                    onSelect(c.id)
                    setConfirmDeleteId(null)
                }
            }}
        >
            <ChatIcon />
            <span className="sidebar__item-title">{c.title}</span>
            {confirmDeleteId === c.id ? (
                <div className="sidebar__item-confirm" onClick={e => e.stopPropagation()}>
                    <button
                        className="sidebar__item-confirm-yes"
                        onClick={e => { e.stopPropagation(); onDelete(c.id); setConfirmDeleteId(null) }}
                        aria-label={`Confirm delete "${c.title}"`}
                    >
                        Delete
                    </button>
                    <button
                        className="sidebar__item-confirm-no"
                        onClick={e => { e.stopPropagation(); setConfirmDeleteId(null) }}
                        aria-label="Cancel delete"
                    >
                        Cancel
                    </button>
                </div>
            ) : (
                <button
                    className="sidebar__item-delete"
                    onClick={e => { e.stopPropagation(); setConfirmDeleteId(c.id) }}
                    aria-label={`Delete "${c.title}"`}
                >
                    <TrashIcon />
                </button>
            )}
        </div>
    )

    return (
        <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`} aria-label="Conversation history">
            <div className="sidebar__header">
                {!collapsed && <span className="sidebar__logo">Fynd AI</span>}
                <button
                    className="sidebar__toggle"
                    onClick={onToggle}
                    aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    aria-expanded={!collapsed}
                >
                    <MenuIcon />
                </button>
            </div>

            {!collapsed && (
                <>
                    <button className="sidebar__new-btn" onClick={onNew} aria-label="Start new chat">
                        <PlusIcon /><span>New chat</span>
                    </button>

                    <div className="sidebar__search">
                        <SearchIcon aria-hidden="true" />
                        <input
                            type="search"
                            placeholder="Search chats…"
                            value={search}
                            onChange={e => onSearch(e.target.value)}
                            aria-label="Search conversations"
                        />
                    </div>

                    <div className="sidebar__list" role="list">
                        {filtered ? (
                            filtered.length > 0
                                ? filtered.map(renderItem)
                                : <div className="sidebar__empty">No results</div>
                        ) : (
                            Object.entries(groups).map(([label, items]) =>
                                items.length === 0 ? null : (
                                    <div key={label} className="sidebar__group">
                                        <div className="sidebar__group-label">{label}</div>
                                        {items.map(renderItem)}
                                    </div>
                                )
                            )
                        )}
                    </div>
                </>
            )}

            {collapsed && (
                <div className="sidebar__collapsed-actions">
                    <button className="sidebar__icon-btn" onClick={onNew} aria-label="Start new chat">
                        <PlusIcon />
                    </button>
                </div>
            )}
        </aside>
    )
}

// ── Upload Panel ─────────────────────────────────────────────────────────────
function UploadPanel() {
    const [state, setState]       = useState('idle')
    const [progress, setProgress] = useState({ done: 0, total: 0, title: '' })
    const [result, setResult]     = useState(null)
    const [dragging, setDragging] = useState(false)
    const inputRef = useRef(null)

    async function handleFile(file) {
        if (!file) return
        const ext = file.name.split('.').pop().toLowerCase()
        if (!['csv', 'xlsx', 'xls'].includes(ext)) {
            setState('error')
            setResult({ message: 'Only CSV or Excel files supported.' })
            return
        }

        setState('uploading')
        setProgress({ done: 0, total: 0, title: '' })
        setResult(null)

        const form = new FormData()
        form.append('file', file)

        try {
            const res = await fetch(`${API_URL}/upload`, { method: 'POST', body: form })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.detail || `Server error ${res.status}`)
            }

            const reader  = res.body.getReader()
            const decoder = new TextDecoder()
            let   buf     = ''

            while (true) {
                const { done, value } = await reader.read()
                if (done) break
                buf += decoder.decode(value, { stream: true })
                const lines = buf.split('\n')
                buf = lines.pop() ?? ''
                for (const line of lines) {
                    if (!line.trim()) continue
                    try {
                        const evt = JSON.parse(line)
                        if (evt.status === 'progress') {
                            setProgress({ done: evt.done, total: evt.total, title: evt.title })
                        } else if (evt.status === 'done') {
                            setState('done')
                            setResult(evt)
                        }
                    } catch {}
                }
            }
        } catch (err) {
            setState('error')
            setResult({ message: err.message })
        }
    }

    function onDrop(e) {
        e.preventDefault()
        setDragging(false)
        handleFile(e.dataTransfer.files[0])
    }

    const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0

    return (
        <div className="upload-panel">
            {state === 'idle' || state === 'error' ? (
                <div
                    className={`upload-zone ${dragging ? 'upload-zone--drag' : ''}`}
                    onClick={() => inputRef.current?.click()}
                    onDragOver={e => { e.preventDefault(); setDragging(true) }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={onDrop}
                    role="button"
                    tabIndex={0}
                    aria-label="Upload product catalog. Accepts CSV or Excel. Click or drag and drop."
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click() }}
                >
                    <span className="upload-zone__icon" aria-hidden="true">⬆</span>
                    <span className="upload-zone__text">CSV or Excel</span>
                    <span className="upload-zone__hint">click or drag & drop</span>
                    <input
                        ref={inputRef}
                        type="file"
                        accept=".csv,.xlsx,.xls"
                        style={{ display: 'none' }}
                        onChange={e => handleFile(e.target.files[0])}
                        aria-hidden="true"
                        tabIndex={-1}
                    />
                </div>
            ) : state === 'uploading' ? (
                <div className="upload-progress" role="status">
                    <div
                        className="upload-progress__bar"
                        role="progressbar"
                        aria-valuenow={pct}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label={`Upload progress: ${pct}%`}
                    >
                        <div className="upload-progress__fill" style={{ width: `${pct}%` }} />
                    </div>
                    <div className="upload-progress__label">
                        Embedding {progress.done}/{progress.total}
                    </div>
                    {progress.title && (
                        <div className="upload-progress__title">{progress.title}</div>
                    )}
                </div>
            ) : state === 'done' ? (
                <div className="upload-result upload-result--ok" role="status">
                    <span>✓ {result?.added} products added</span>
                    {result?.skipped > 0 && <span className="upload-result__skip">{result.skipped} skipped</span>}
                    <button className="upload-result__reset" onClick={() => setState('idle')}>Upload more</button>
                </div>
            ) : null}

            {state === 'error' && (
                <div className="upload-result upload-result--err" role="alert">
                    {result?.message || 'Upload failed'}
                </div>
            )}
        </div>
    )
}

// ── Settings Modal ────────────────────────────────────────────────────────────
function SettingsModal({ personality, setPersonality, customPrompt, setCustomPrompt, onClose }) {
    const modalRef = useRef(null)

    useEffect(() => {
        // Move focus into modal on open
        const focusable = modalRef.current?.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
        focusable?.[0]?.focus()

        const handleKey = (e) => {
            if (e.key === 'Escape') { onClose(); return }
            if (e.key === 'Tab' && modalRef.current) {
                const els = Array.from(modalRef.current.querySelectorAll(
                    'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
                ))
                const first = els[0]
                const last  = els[els.length - 1]
                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault(); last.focus()
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault(); first.focus()
                }
            }
        }
        document.addEventListener('keydown', handleKey)
        return () => document.removeEventListener('keydown', handleKey)
    }, [onClose])

    return (
        <div className="modal-overlay" onClick={onClose} aria-hidden="true">
            <div
                className="modal"
                ref={modalRef}
                onClick={e => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-labelledby="settings-title"
            >
                <div className="modal__header">
                    <h2 id="settings-title">Settings</h2>
                    <button className="modal__close" onClick={onClose} aria-label="Close settings">
                        <CloseIcon />
                    </button>
                </div>
                <div className="modal__content">
                    <p className="modal__description">Choose how the AI assistant responds:</p>
                    <div className="personality-grid">
                        {Object.entries(PERSONALITIES).map(([key, desc]) => (
                            <button
                                key={key}
                                className={`personality-card ${personality === key ? 'personality-card--active' : ''}`}
                                onClick={() => { setPersonality(key); onClose() }}
                                aria-pressed={personality === key}
                            >
                                <div className="personality-card__name">
                                    {key.charAt(0).toUpperCase() + key.slice(1)}
                                </div>
                                <div className="personality-card__desc">{desc.split('.')[0]}.</div>
                            </button>
                        ))}
                    </div>
                    <div className="custom-section">
                        <div className="custom-section__header">
                            <h3>Custom Instructions</h3>
                            <button
                                className={`custom-toggle ${personality === 'custom' ? 'custom-toggle--active' : ''}`}
                                onClick={() => setPersonality('custom')}
                                aria-pressed={personality === 'custom'}
                            >
                                {personality === 'custom' ? '✓ Active' : 'Use Custom'}
                            </button>
                        </div>
                        <textarea
                            className="custom-textarea"
                            value={customPrompt}
                            onChange={e => setCustomPrompt(e.target.value)}
                            placeholder="Enter your custom instructions…"
                            rows={4}
                            aria-label="Custom system instructions"
                        />
                    </div>

                    <div className="catalog-section">
                        <div className="catalog-section__header">
                            <h3>Product Catalog</h3>
                            <p className="catalog-section__desc">Import products to make them searchable in chat.</p>
                        </div>
                        <UploadPanel />
                    </div>
                </div>
            </div>
        </div>
    )
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
    const [convos, setConvos] = useState(() => {
        const stored = loadConversations()
        if (stored[0]?.messages?.length > 0) return [newConversation(), ...stored]
        return stored
    })
    const [activeId, setActiveId] = useState(() => convos[0].id)

    const [input, setInput]               = useState('')
    const [loading, setLoading]           = useState(false)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
    const [showSettings, setShowSettings] = useState(false)
    const [personality, setPersonality]   = useState('helpful')
    const [customPrompt, setCustomPrompt] = useState('You are a helpful AI assistant.')
    const [search, setSearch]             = useState('')
    const [showScrollBtn, setShowScrollBtn] = useState(false)

    const textareaRef    = useRef(null)
    const bottomRef      = useRef(null)
    const chatRef        = useRef(null)
    const abortRef       = useRef(null)
    const fileRef        = useRef(null)
    const userScrolledUp = useRef(false)
    const tokenBufferRef = useRef('')
    const flushTimerRef  = useRef(null)
    const saveTimerRef   = useRef(null)

    const activeConvo = convos.find(c => c.id === activeId) ?? convos[0]
    const messages    = activeConvo?.messages ?? []
    const isHome      = messages.length === 0

    useEffect(() => {
        clearTimeout(saveTimerRef.current)
        saveTimerRef.current = setTimeout(() => saveConversations(convos), 1000)
        return () => clearTimeout(saveTimerRef.current)
    }, [convos])

    useEffect(() => {
        const ta = textareaRef.current
        if (!ta) return
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }, [input])

    useEffect(() => {
        if (!userScrolledUp.current) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    const handleChatScroll = useCallback(() => {
        const el = chatRef.current
        if (!el) return
        const isUp = el.scrollHeight - el.scrollTop - el.clientHeight > 100
        userScrolledUp.current = isUp
        setShowScrollBtn(isUp)
    }, [])

    const updateConvo = useCallback((id, updater) => {
        setConvos(prev => prev.map(c => c.id === id ? { ...updater(c), updatedAt: Date.now() } : c))
    }, [])

    const stopFlush = useCallback((currentId) => {
        if (flushTimerRef.current) {
            clearInterval(flushTimerRef.current)
            flushTimerRef.current = null
        }
        if (tokenBufferRef.current) {
            const remaining = tokenBufferRef.current
            tokenBufferRef.current = ''
            updateConvo(currentId, c => {
                const msgs = [...c.messages]
                const last = msgs[msgs.length - 1]
                msgs[msgs.length - 1] = { ...last, content: last.content + remaining }
                return { ...c, messages: msgs }
            })
        }
    }, [updateConvo])

    const handleNew = () => {
        const c = newConversation()
        setConvos(prev => [c, ...prev])
        setActiveId(c.id)
        setInput('')
        userScrolledUp.current = false
        setShowScrollBtn(false)
        if (window.innerWidth <= 768) setSidebarCollapsed(true)
    }

    const handleDelete = (id) => {
        setConvos(prev => {
            const next = prev.filter(c => c.id !== id)
            if (next.length === 0) {
                const fresh = newConversation()
                setActiveId(fresh.id)
                return [fresh]
            }
            if (id === activeId) setActiveId(next[0].id)
            return next
        })
    }

    const handleSelect = (id) => {
        setActiveId(id)
        userScrolledUp.current = false
        setShowScrollBtn(false)
        if (window.innerWidth <= 768) setSidebarCollapsed(true)
    }

    const handleExport = useCallback(() => {
        const msgs = activeConvo?.messages ?? []
        const lines = [`# ${activeConvo?.title ?? 'Chat'}\n\n`]
        for (const m of msgs) {
            if (!m.content || m.loading || m.streaming) continue
            lines.push(`**${m.role === 'user' ? 'You' : 'Assistant'}**\n\n${m.content}\n\n---\n\n`)
        }
        const blob = new Blob([lines.join('')], { type: 'text/markdown' })
        const url  = URL.createObjectURL(blob)
        const a    = document.createElement('a')
        a.href     = url
        a.download = `${(activeConvo?.title ?? 'chat').replace(/[^a-z0-9]/gi, '-').toLowerCase()}.md`
        a.click()
        URL.revokeObjectURL(url)
    }, [activeConvo])

    const handleRetry = useCallback((retryContent) => {
        updateConvo(activeId, c => {
            const msgs = [...c.messages]
            while (msgs.length > 0 && msgs[msgs.length - 1].error) msgs.pop()
            if (msgs.length > 0 && msgs[msgs.length - 1].role === 'user') msgs.pop()
            return { ...c, messages: msgs }
        })
        setInput(retryContent)
        setTimeout(() => textareaRef.current?.focus(), 50)
    }, [activeId, updateConvo])

    const scrollToBottom = () => {
        userScrolledUp.current = false
        setShowScrollBtn(false)
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }

    const handleSubmit = async (text = input) => {
        const question = text.trim()
        if (!question || loading) return

        userScrolledUp.current = false
        setShowScrollBtn(false)
        setInput('')
        tokenBufferRef.current = ''

        const currentId = activeId
        const history = messages
            .filter(m => !m.loading && !m.streaming && m.content)
            .map(({ role, content }) => ({ role, content }))

        const now     = Date.now()
        const userMsg = { role: 'user',      content: question, id: genId(), ts: now }
        const botMsg  = { role: 'assistant', content: '',        id: genId(), loading: true, ts: now }

        const isFirstMsg = messages.filter(m => m.role === 'user').length === 0
        const title = isFirstMsg
            ? question.slice(0, 60) + (question.length > 60 ? '…' : '')
            : activeConvo.title

        updateConvo(currentId, c => ({
            ...c,
            title,
            messages: [...c.messages, userMsg, botMsg],
        }))

        setLoading(true)
        const messagesPayload = [...history, { role: 'user', content: question }]

        try {
            const controller = new AbortController()
            abortRef.current = controller

            const res = await fetch(`${API_URL}/ask/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: messagesPayload,
                    personality: personality === 'custom' ? customPrompt : PERSONALITIES[personality],
                }),
                signal: controller.signal,
            })

            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.error || err.detail || `Server error ${res.status}`)
            }

            const reader  = res.body.getReader()
            const decoder = new TextDecoder()
            let buffer    = ''
            let firstToken = true

            while (true) {
                const { done, value } = await reader.read()
                if (done) break

                buffer += decoder.decode(value, { stream: true })
                const lines = buffer.split('\n')
                buffer = lines.pop() ?? ''

                for (const line of lines) {
                    if (!line.startsWith('data: ')) continue
                    let event
                    try { event = JSON.parse(line.slice(6)) } catch { continue }

                    if (event.type === 'tool_call') {
                        updateConvo(currentId, c => {
                            const msgs = [...c.messages]
                            const last = msgs[msgs.length - 1]
                            const calls = [...(last.toolCalls || []), { tool: event.tool, query: event.query, found: null }]
                            msgs[msgs.length - 1] = { ...last, toolCalls: calls }
                            return { ...c, messages: msgs }
                        })

                    } else if (event.type === 'tool_result') {
                        updateConvo(currentId, c => {
                            const msgs = [...c.messages]
                            const last = msgs[msgs.length - 1]
                            const calls = (last.toolCalls || []).map((tc, i) =>
                                i === (last.toolCalls.length - 1) ? { ...tc, found: event.found } : tc
                            )
                            msgs[msgs.length - 1] = { ...last, toolCalls: calls }
                            return { ...c, messages: msgs }
                        })

                    } else if (event.type === 'token' && event.content) {
                        if (firstToken) {
                            firstToken = false
                            updateConvo(currentId, c => {
                                const msgs = [...c.messages]
                                msgs[msgs.length - 1] = {
                                    ...msgs[msgs.length - 1],
                                    loading: false,
                                    streaming: true,
                                    content: '',
                                }
                                return { ...c, messages: msgs }
                            })
                            flushTimerRef.current = setInterval(() => {
                                if (tokenBufferRef.current) {
                                    const chunk = tokenBufferRef.current
                                    tokenBufferRef.current = ''
                                    updateConvo(currentId, c => {
                                        const msgs = [...c.messages]
                                        const last = msgs[msgs.length - 1]
                                        msgs[msgs.length - 1] = { ...last, content: last.content + chunk }
                                        return { ...c, messages: msgs }
                                    })
                                }
                            }, 50)
                        }
                        tokenBufferRef.current += event.content

                    } else if (event.type === 'done') {
                        stopFlush(currentId)
                        updateConvo(currentId, c => {
                            const msgs = [...c.messages]
                            msgs[msgs.length - 1] = {
                                ...msgs[msgs.length - 1],
                                streaming: false,
                                ttft_ms: event.ttft_ms ?? null,
                            }
                            return { ...c, messages: msgs }
                        })

                    } else if (event.type === 'error') {
                        stopFlush(currentId)
                        updateConvo(currentId, c => {
                            const msgs = [...c.messages]
                            msgs[msgs.length - 1] = {
                                ...msgs[msgs.length - 1],
                                content: event.message,
                                loading: false,
                                streaming: false,
                                error: true,
                                retryContent: question,
                            }
                            return { ...c, messages: msgs }
                        })
                    }
                }
            }
        } catch (err) {
            stopFlush(currentId)
            if (err.name === 'AbortError') return
            updateConvo(currentId, c => {
                const msgs = [...c.messages]
                msgs[msgs.length - 1] = {
                    ...msgs[msgs.length - 1],
                    content: err.message || 'Could not reach the server.',
                    loading: false,
                    streaming: false,
                    error: true,
                    retryContent: question,
                }
                return { ...c, messages: msgs }
            })
        } finally {
            stopFlush(currentId)
            setLoading(false)
            abortRef.current = null
        }
    }

    const handleStop = () => {
        abortRef.current?.abort()
        setLoading(false)
        updateConvo(activeId, c => {
            const msgs = [...c.messages]
            const last = msgs[msgs.length - 1]
            if (last?.loading || last?.streaming) {
                msgs[msgs.length - 1] = {
                    ...last,
                    content: last.content || '(stopped)',
                    loading: false,
                    streaming: false,
                }
            }
            return { ...c, messages: msgs }
        })
    }

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSubmit() }
    }

    const handleFileChange = (e) => {
        const files = Array.from(e.target.files)
        if (!files.length) return
        setInput(prev => (prev ? prev + ' ' : '') + `[${files.map(f => f.name).join(', ')}]`)
        textareaRef.current?.focus()
        e.target.value = ''
    }

    const charCountClass = input.length > 1800
        ? 'char-counter char-counter--danger'
        : input.length > 1200
            ? 'char-counter char-counter--warn'
            : 'char-counter'

    return (
        <div className="app">
            {/* Screen-reader live region for AI response state */}
            <div aria-live="polite" aria-atomic="true" className="sr-only">
                {loading ? 'Fynd AI is responding…' : ''}
            </div>

            {/* Mobile sidebar overlay */}
            {!sidebarCollapsed && (
                <div
                    className="sidebar-overlay"
                    onClick={() => setSidebarCollapsed(true)}
                    aria-hidden="true"
                />
            )}

            <Sidebar
                convos={convos}
                activeId={activeId}
                onSelect={handleSelect}
                onNew={handleNew}
                onDelete={handleDelete}
                collapsed={sidebarCollapsed}
                onToggle={() => setSidebarCollapsed(v => !v)}
                search={search}
                onSearch={setSearch}
            />

            <div className="workspace">
                <header className="topbar">
                    <button
                        className="topbar__menu-btn"
                        onClick={() => setSidebarCollapsed(false)}
                        aria-label="Open navigation"
                    >
                        <MenuIcon />
                    </button>
                    <span className="topbar__title">{!isHome && activeConvo?.title}</span>
                    <div className="topbar__actions">
                        {!isHome && messages.some(m => m.content && !m.loading) && (
                            <button className="topbar__btn" onClick={handleExport}>
                                <ExportIcon /> Export
                            </button>
                        )}
                        <button
                            className="topbar__icon-btn topbar__icon-btn--settings"
                            onClick={() => setShowSettings(true)}
                            aria-label="Open settings"
                        >
                            <SettingsIcon />
                        </button>
                    </div>
                </header>

                <main className={`main ${isHome ? 'main--home' : 'main--chat'}`}>
                    {isHome ? (
                        <div className="home">
                            <img src="/logo.png" alt="Fynd AI" className="home__logo" />
                            <h1 className="home__heading">What can I help with?</h1>
                            <SuggestedPrompts onSelect={t => { setInput(t); textareaRef.current?.focus() }} />
                        </div>
                    ) : (
                        <div className="chat" ref={chatRef} onScroll={handleChatScroll}>
                            {messages.map((msg) => (
                                <Message
                                    key={msg.id}
                                    msg={msg}
                                    onRetry={msg.error ? () => handleRetry(msg.retryContent) : null}
                                />
                            ))}
                            <div ref={bottomRef} />
                        </div>
                    )}

                    {showScrollBtn && (
                        <button className="scroll-btn" onClick={scrollToBottom} aria-label="Scroll to bottom">
                            <ChevronDownIcon />
                        </button>
                    )}

                    <div className={`composer-wrap ${isHome ? 'composer-wrap--home' : 'composer-wrap--bottom'}`}>
                        <div className="composer">
                            <textarea
                                ref={textareaRef}
                                className="composer__input"
                                placeholder="Ask anything… (Shift+Enter for new line)"
                                value={input}
                                onChange={e => setInput(e.target.value)}
                                onKeyDown={handleKeyDown}
                                maxLength={MAX_INPUT_CHARS}
                                rows={1}
                                aria-label="Message input"
                                aria-describedby={input.length > 800 ? 'char-count' : undefined}
                            />
                            <div className="composer__toolbar">
                                <div className="composer__toolbar-left">
                                    <input
                                        ref={fileRef}
                                        type="file"
                                        multiple
                                        style={{ display: 'none' }}
                                        onChange={handleFileChange}
                                        aria-hidden="true"
                                        tabIndex={-1}
                                    />
                                    <button
                                        className="icon-btn"
                                        aria-label="Attach file"
                                        onClick={() => fileRef.current?.click()}
                                    >
                                        <AttachIcon />
                                    </button>
                                    {input.length > 800 && (
                                        <span id="char-count" className={charCountClass} aria-live="polite">
                                            {input.length}/{MAX_INPUT_CHARS}
                                        </span>
                                    )}
                                </div>
                                <div className="composer__toolbar-right">
                                    {loading ? (
                                        <button
                                            className="send-btn send-btn--stop"
                                            onClick={handleStop}
                                            aria-label="Stop generation"
                                        >
                                            <StopIcon />
                                        </button>
                                    ) : (
                                        <button
                                            className={`send-btn ${input.trim() ? 'send-btn--active' : ''}`}
                                            onClick={() => handleSubmit()}
                                            disabled={!input.trim()}
                                            aria-label="Send message"
                                        >
                                            <SendIcon />
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </main>
            </div>

            {showSettings && (
                <SettingsModal
                    personality={personality}
                    setPersonality={setPersonality}
                    customPrompt={customPrompt}
                    setCustomPrompt={setCustomPrompt}
                    onClose={() => setShowSettings(false)}
                />
            )}
        </div>
    )
}
