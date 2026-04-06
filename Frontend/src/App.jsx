import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

// ── Icons ────────────────────────────────────────────────────────────────────
const PlusIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
)
const SendIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
)
const StopIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
)
const UserIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
    </svg>
)
const BotIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
        <line x1="12" y1="3" x2="12" y2="7" />
        <circle cx="8.5" cy="16" r="1" fill="currentColor" stroke="none" />
        <circle cx="15.5" cy="16" r="1" fill="currentColor" stroke="none" />
        <path d="M9.5 19.5c.8.5 2.5.5 3 0" />
    </svg>
)
const SettingsIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v6m0 6v6M5.6 5.6l4.2 4.2m4.4 4.4l4.2 4.2M1 12h6m6 0h6M5.6 18.4l4.2-4.2m4.4-4.4l4.2-4.2" />
    </svg>
)
const CloseIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
)
const CopyIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
)
const CheckIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
    </svg>
)
const TrashIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
        <path d="M10 11v6M14 11v6" />
        <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
)

// ── Markdown code block renderer ──────────────────────────────────────────────
function CodeBlock({ inline, className, children, ...props }) {
    const [copied, setCopied] = useState(false)
    const code = String(children).replace(/\n$/, '')
    const lang  = /language-(\w+)/.exec(className || '')?.[1] ?? 'text'

    if (inline) {
        return <code className="inline-code" {...props}>{children}</code>
    }

    const handleCopy = () => {
        navigator.clipboard.writeText(code)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }

    return (
        <div className="code-block">
            <div className="code-block__header">
                <span className="code-block__lang">{lang}</span>
                <button className="code-block__copy" onClick={handleCopy}>
                    {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
                </button>
            </div>
            <SyntaxHighlighter
                style={oneDark}
                language={lang}
                PreTag="div"
                customStyle={{ margin: 0, borderRadius: '0 0 8px 8px', fontSize: '13px' }}
                {...props}
            >
                {code}
            </SyntaxHighlighter>
        </div>
    )
}

// Markdown component map — reused across all messages
const MD_COMPONENTS = {
    code: CodeBlock,
    // Open links in new tab safely
    a: ({ href, children }) => (
        <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
    ),
}

// ── Typing indicator ──────────────────────────────────────────────────────────
function TypingDots() {
    return (
        <span className="typing-dots">
            <span /><span /><span />
        </span>
    )
}

// ── Message ───────────────────────────────────────────────────────────────────
function Message({ msg }) {
    const [copied, setCopied] = useState(false)

    const handleCopy = () => {
        navigator.clipboard.writeText(msg.content)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }

    return (
        <div className={`message message--${msg.role}`}>
            <div className="message__avatar">
                {msg.role === 'user' ? <UserIcon /> : <BotIcon />}
            </div>
            <div className="message__body">
                <div className="message__bubble">
                    {msg.loading ? (
                        <TypingDots />
                    ) : (
                        <>
                            {msg.role === 'assistant' ? (
                                <div className="message__markdown">
                                    <ReactMarkdown
                                        remarkPlugins={[remarkGfm]}
                                        components={MD_COMPONENTS}
                                    >
                                        {msg.content}
                                    </ReactMarkdown>
                                    {/* Blinking cursor while streaming */}
                                    {msg.streaming && <span className="stream-cursor" />}
                                </div>
                            ) : (
                                <span className="message__text">{msg.content}</span>
                            )}
                        </>
                    )}
                </div>

                {/* Copy button — only on completed assistant messages */}
                {msg.role === 'assistant' && !msg.loading && !msg.streaming && msg.content && (
                    <div className="message__actions">
                        <button className="action-btn" onClick={handleCopy} title="Copy response">
                            {copied ? <><CheckIcon /> Copied</> : <><CopyIcon /> Copy</>}
                        </button>
                        {msg.ttft_ms && (
                            <span className="ttft-badge">{msg.ttft_ms}ms</span>
                        )}
                    </div>
                )}
            </div>
        </div>
    )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const STORAGE_KEY = 'ollama_chat_history'

function loadHistory() {
    try {
        return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
    } catch {
        return []
    }
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
    const [input, setInput]               = useState('')
    const [messages, setMessages]         = useState(loadHistory)
    const [loading, setLoading]           = useState(false)
    const [showSettings, setShowSettings] = useState(false)
    const [personality, setPersonality]   = useState('helpful')
    const [customPrompt, setCustomPrompt] = useState('You are a helpful AI assistant.')

    const textareaRef    = useRef(null)
    const bottomRef      = useRef(null)
    const chatRef        = useRef(null)
    const abortRef       = useRef(null)
    const fileRef        = useRef(null)
    const userScrolledUp = useRef(false)   // true when user has scrolled away from bottom

    const isHome = messages.length === 0

    const personalities = {
        helpful:      'You are a helpful assistant. Answer clearly and accurately.',
        creative:     'You are a creative assistant. Give imaginative and original answers.',
        technical:    'You are a technical assistant. Give precise, detailed explanations with correct terminology.',
        casual:       'You are a casual, friendly assistant. Use simple, natural language.',
        professional: 'You are a professional assistant. Give structured, concise, business-focused answers.',
        custom:       customPrompt,
    }

    // Persist chat history to localStorage whenever it changes
    useEffect(() => {
        // Don't persist loading / streaming states
        const toSave = messages.filter(m => !m.loading && !m.streaming)
        localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
    }, [messages])

    // Auto-resize textarea
    useEffect(() => {
        const ta = textareaRef.current
        if (!ta) return
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }, [input])

    // Auto-scroll — only if user hasn't scrolled up
    useEffect(() => {
        if (!userScrolledUp.current) {
            bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
        }
    }, [messages])

    // Track whether user has scrolled away from the bottom
    const handleChatScroll = useCallback(() => {
        const el = chatRef.current
        if (!el) return
        const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
        userScrolledUp.current = distFromBottom > 80
    }, [])

    const clearHistory = () => {
        setMessages([])
        localStorage.removeItem(STORAGE_KEY)
    }

    const handleSubmit = async (text = input) => {
        const question = text.trim()
        if (!question || loading) return

        userScrolledUp.current = false   // snap back to bottom on new message
        setInput('')

        // Build history payload — exclude any transient UI states
        const history = messages
            .filter(m => !m.loading && !m.streaming && m.content)
            .map(({ role, content }) => ({ role, content }))

        const userMsg = { role: 'user',      content: question,  id: Date.now() }
        const botMsg  = { role: 'assistant', content: '',         id: Date.now() + 1, loading: true }

        setMessages(prev => [...prev, userMsg, botMsg])
        setLoading(true)

        // Full messages to send = history + new user message
        const messagesPayload = [...history, { role: 'user', content: question }]

        try {
            const controller = new AbortController()
            abortRef.current = controller

            const res = await fetch(`${API_URL}/ask/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: messagesPayload,
                    personality: personality === 'custom' ? customPrompt : personalities[personality],
                }),
                signal: controller.signal,
            })

            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.error || err.detail || `Server error ${res.status}`)
            }

            const reader     = res.body.getReader()
            const decoder    = new TextDecoder()
            let buffer       = ''
            let firstToken   = true
            let finalTtft    = null

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

                    if (event.type === 'token' && event.content) {
                        if (firstToken) {
                            firstToken = false
                            // Replace loading placeholder with first token
                            setMessages(prev => {
                                const next = [...prev]
                                next[next.length - 1] = {
                                    ...next[next.length - 1],
                                    loading:   false,
                                    streaming: true,
                                    content:   event.content,
                                }
                                return next
                            })
                        } else {
                            setMessages(prev => {
                                const next = [...prev]
                                const last = next[next.length - 1]
                                next[next.length - 1] = {
                                    ...last,
                                    content: last.content + event.content,
                                }
                                return next
                            })
                        }

                    } else if (event.type === 'done') {
                        finalTtft = event.ttft_ms ?? null
                        // Mark streaming complete, attach TTFT badge
                        setMessages(prev => {
                            const next = [...prev]
                            next[next.length - 1] = {
                                ...next[next.length - 1],
                                streaming: false,
                                ttft_ms:   finalTtft,
                            }
                            return next
                        })

                    } else if (event.type === 'blocked') {
                        setMessages(prev => {
                            const next = [...prev]
                            next[next.length - 1] = {
                                ...next[next.length - 1],
                                content:   event.refusal,
                                loading:   false,
                                streaming: false,
                            }
                            return next
                        })

                    } else if (event.type === 'error') {
                        setMessages(prev => {
                            const next = [...prev]
                            next[next.length - 1] = {
                                ...next[next.length - 1],
                                content:   event.message,
                                loading:   false,
                                streaming: false,
                            }
                            return next
                        })
                    }
                }
            }

        } catch (err) {
            if (err.name === 'AbortError') return
            const errorMsg = err.message || 'Could not reach the server.'
            setMessages(prev => {
                const next = [...prev]
                next[next.length - 1] = {
                    role: 'assistant', content: errorMsg, loading: false, streaming: false, id: Date.now() + 3,
                }
                return next
            })
        } finally {
            setLoading(false)
            abortRef.current = null
        }
    }

    const handleStop = () => {
        abortRef.current?.abort()
        setLoading(false)
        setMessages(prev => {
            const next = [...prev]
            const last = next[next.length - 1]
            if (last?.loading || last?.streaming) {
                next[next.length - 1] = { ...last, content: last.content || '(stopped)', loading: false, streaming: false }
            }
            return next
        })
    }

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSubmit()
        }
    }

    const handleFileChange = (e) => {
        const files = Array.from(e.target.files)
        if (!files.length) return
        setInput(prev => (prev ? prev + ' ' : '') + `[${files.map(f => f.name).join(', ')}]`)
        textareaRef.current?.focus()
        e.target.value = ''
    }

    return (
        <div className="app">
            {/* Settings button */}
            <button className="settings-btn" onClick={() => setShowSettings(true)} title="Settings">
                <SettingsIcon />
            </button>

            {/* Clear history button — only shown when there are messages */}
            {messages.length > 0 && !loading && (
                <button className="clear-btn" onClick={clearHistory} title="Clear chat">
                    <TrashIcon />
                </button>
            )}

            {/* Settings Modal */}
            {showSettings && (
                <div className="modal-overlay" onClick={() => setShowSettings(false)}>
                    <div className="modal" onClick={e => e.stopPropagation()}>
                        <div className="modal__header">
                            <h2>AI Personality</h2>
                            <button className="modal__close" onClick={() => setShowSettings(false)}>
                                <CloseIcon />
                            </button>
                        </div>
                        <div className="modal__content">
                            <p className="modal__description">Choose how the AI assistant responds to you:</p>
                            <div className="personality-grid">
                                {Object.keys(personalities).filter(k => k !== 'custom').map(key => (
                                    <button
                                        key={key}
                                        className={`personality-card ${personality === key ? 'personality-card--active' : ''}`}
                                        onClick={() => { setPersonality(key); setShowSettings(false) }}
                                    >
                                        <div className="personality-card__name">
                                            {key.charAt(0).toUpperCase() + key.slice(1)}
                                        </div>
                                        <div className="personality-card__desc">
                                            {personalities[key].split('.')[0]}.
                                        </div>
                                    </button>
                                ))}
                            </div>
                            <div className="custom-section">
                                <div className="custom-section__header">
                                    <h3>Custom Instructions</h3>
                                    <button
                                        className={`custom-toggle ${personality === 'custom' ? 'custom-toggle--active' : ''}`}
                                        onClick={() => setPersonality('custom')}
                                    >
                                        {personality === 'custom' ? '✓ Active' : 'Use Custom'}
                                    </button>
                                </div>
                                <p className="custom-section__desc">Define your own instructions for how the AI should behave:</p>
                                <textarea
                                    className="custom-textarea"
                                    value={customPrompt}
                                    onChange={e => setCustomPrompt(e.target.value)}
                                    placeholder="Enter your custom instructions here..."
                                    rows={4}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* Main area */}
            <main className={`main ${isHome ? 'main--home' : 'main--chat'}`}>
                {isHome ? (
                    <div className="home">
                        <img src="/logo.png" alt="Logo" className="home__logo" />
                        <h1 className="home__heading">What can I do for you?</h1>
                    </div>
                ) : (
                    <div className="chat" ref={chatRef} onScroll={handleChatScroll}>
                        {messages.map(msg => <Message key={msg.id} msg={msg} />)}
                        <div ref={bottomRef} />
                    </div>
                )}

                {/* Input area */}
                <div className={`composer-wrap ${isHome ? 'composer-wrap--home' : 'composer-wrap--bottom'}`}>
                    <div className="composer">
                        <textarea
                            ref={textareaRef}
                            className="composer__input"
                            placeholder="Assign a task or ask anything"
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            rows={1}
                        />
                        <div className="composer__toolbar">
                            <div className="composer__toolbar-left">
                                <input ref={fileRef} type="file" multiple style={{ display: 'none' }} onChange={handleFileChange} />
                                <button className="icon-btn" title="Attach file" onClick={() => fileRef.current?.click()}>
                                    <PlusIcon />
                                </button>
                            </div>
                            <div className="composer__toolbar-right">
                                {loading ? (
                                    <button className="send-btn send-btn--stop" onClick={handleStop} title="Stop">
                                        <StopIcon />
                                    </button>
                                ) : (
                                    <button
                                        className={`send-btn ${input.trim() ? 'send-btn--active' : ''}`}
                                        onClick={() => handleSubmit()}
                                        disabled={!input.trim()}
                                        title="Send"
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
    )
}
