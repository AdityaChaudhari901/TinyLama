import { useState, useRef, useEffect } from 'react'
import './App.css'

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8080'

// Icons as inline SVGs to avoid extra deps
const PlusIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
)

const AttachIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
    </svg>
)

const BrowseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8" />
        <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
)

const MicIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
)

const EmojiIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <path d="M8 13s1.5 2 4 2 4-2 4-2" />
        <line x1="9" y1="9" x2="9.01" y2="9" />
        <line x1="15" y1="9" x2="15.01" y2="9" />
    </svg>
)

const SendIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="22" y1="2" x2="11" y2="13" />
        <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
)

const StopIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
)

const UserIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
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
        <line x1="18" y1="6" x2="6" y2="18" />
        <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
)


function TypingDots() {
    return (
        <span className="typing-dots">
            <span /><span /><span />
        </span>
    )
}

function Message({ msg }) {
    return (
        <div className={`message message--${msg.role}`}>
            <div className="message__avatar">
                {msg.role === 'user' ? <UserIcon /> : <BotIcon />}
            </div>
            <div className="message__bubble">
                {msg.loading ? <TypingDots /> : <span className="message__text">{msg.content}</span>}
            </div>
        </div>
    )
}

export default function App() {
    const [input, setInput] = useState('')
    const [messages, setMessages] = useState([])
    const [loading, setLoading] = useState(false)
    const [showSettings, setShowSettings] = useState(false)
    const [personality, setPersonality] = useState('helpful')
    const [customPrompt, setCustomPrompt] = useState('You are a helpful AI assistant.')
    const textareaRef = useRef(null)
    const bottomRef = useRef(null)
    const abortRef = useRef(null)
    const fileRef = useRef(null)

    const isHome = messages.length === 0

    // Personality presets
    // Personality presets (LLM-optimized)
const personalities = {
    helpful: `
You are a reliable and friendly AI assistant.
Provide clear, accurate, and well-structured answers.
Be concise by default, but expand when helpful.
Maintain a respectful and positive tone.
If information is uncertain or unavailable, state that clearly.
Do not fabricate facts.
Prioritize practical and actionable guidance.
`,

    creative: `
You are an imaginative and expressive AI assistant.
Generate original ideas and thoughtful perspectives.
Use vivid language when appropriate, but remain coherent and relevant.
Balance creativity with clarity and usefulness.
Avoid unnecessary fluff or confusion.
`,

    technical: `
You are a precise and technically rigorous AI assistant.
Provide accurate, detailed, and logically structured explanations.
Use correct terminology and define complex concepts clearly.
Show step-by-step reasoning when useful.
Acknowledge assumptions and uncertainty explicitly.
Do not speculate beyond available information.
`,

    casual: `
You are a relaxed and conversational AI assistant.
Use simple, natural language and keep responses easy to read.
Be friendly and approachable without being unprofessional.
Keep explanations clear and practical.
Avoid slang that may reduce clarity.
`,

    professional: `
You are a formal and business-oriented AI assistant.
Deliver structured, concise, and results-focused responses.
Use clear formatting when helpful (bullet points, steps, summaries).
Maintain a neutral and professional tone.
Prioritize efficiency, accuracy, and decision-support.
`,

    custom: customPrompt
};

    // Auto-resize textarea
    useEffect(() => {
        const ta = textareaRef.current
        if (!ta) return
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 200) + 'px'
    }, [input])

    // Scroll to bottom on new message
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    const handleSubmit = async (text = input) => {
        const question = text.trim()
        if (!question || loading) return

        setInput('')
        setMessages(prev => [
            ...prev,
            { role: 'user', content: question, id: Date.now() },
            { role: 'assistant', content: '', loading: true, id: Date.now() + 1 },
        ])
        setLoading(true)

        try {
            const controller = new AbortController()
            abortRef.current = controller

            const res = await fetch(`${API_URL}/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    question,
                    personality: personality === 'custom' ? customPrompt : personalities[personality]
                }),
                signal: controller.signal,
            })

            if (!res.ok) {
                const error = await res.json().catch(() => ({}))
                throw new Error(error.error || error.detail || `Server error ${res.status}`)
            }
            const data = await res.json()

            setMessages(prev => {
                const next = [...prev]
                next[next.length - 1] = { role: 'assistant', content: data.answer, id: Date.now() + 2 }
                return next
            })
        } catch (err) {
            if (err.name === 'AbortError') return
            const errorMsg = err.message || '⚠️ Could not reach the server. Check the backend is running.'
            setMessages(prev => {
                const next = [...prev]
                next[next.length - 1] = {
                    role: 'assistant',
                    content: `⚠️ ${errorMsg}`,
                    id: Date.now() + 3,
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
            if (next[next.length - 1]?.loading) {
                next[next.length - 1] = { ...next[next.length - 1], content: '(stopped)', loading: false }
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
        const names = files.map(f => f.name).join(', ')
        setInput(prev => (prev ? prev + ' ' : '') + `[${names}]`)
        textareaRef.current?.focus()
        e.target.value = '' // reset so same file can be picked again
    }

    return (
        <div className="app">
            {/* Settings button - top left */}
            <button 
                className="settings-btn" 
                onClick={() => setShowSettings(true)}
                title="Settings"
            >
                <SettingsIcon />
            </button>

            {/* Settings Modal */}
            {showSettings && (
                <div className="modal-overlay" onClick={() => setShowSettings(false)}>
                    <div className="modal" onClick={(e) => e.stopPropagation()}>
                        <div className="modal__header">
                            <h2>AI Personality</h2>
                            <button className="modal__close" onClick={() => setShowSettings(false)}>
                                <CloseIcon />
                            </button>
                        </div>
                        <div className="modal__content">
                            <p className="modal__description">
                                Choose how the AI assistant responds to you:
                            </p>
                            <div className="personality-grid">
                                {Object.keys(personalities).filter(k => k !== 'custom').map(key => (
                                    <button
                                        key={key}
                                        className={`personality-card ${personality === key ? 'personality-card--active' : ''}`}
                                        onClick={() => {
                                            setPersonality(key)
                                            setShowSettings(false)
                                        }}
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
                                <p className="custom-section__desc">
                                    Define your own instructions for how the AI should behave:
                                </p>
                                <textarea
                                    className="custom-textarea"
                                    value={customPrompt}
                                    onChange={(e) => setCustomPrompt(e.target.value)}
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
                        <img
                            src="/logo.png"
                            alt="Logo"
                            className="home__logo"
                        />
                        <h1 className="home__heading">What can I do for you?</h1>
                    </div>
                ) : (
                    <div className="chat">
                        {messages.map(msg => (
                            <Message key={msg.id} msg={msg} />
                        ))}
                        <div ref={bottomRef} />
                    </div>
                )}

                {/* Input area */}
                <div className={`composer-wrap ${isHome ? 'composer-wrap--home' : 'composer-wrap--bottom'}`}>
                    <div className="composer">
                        {/* Textarea */}
                        <textarea
                            ref={textareaRef}
                            className="composer__input"
                            placeholder="Assign a task or ask anything"
                            value={input}
                            onChange={e => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            rows={1}
                        />

                        {/* Bottom toolbar */}
                        <div className="composer__toolbar">
                            <div className="composer__toolbar-left">
                                {/* Hidden file input */}
                                <input
                                    ref={fileRef}
                                    type="file"
                                    multiple
                                    style={{ display: 'none' }}
                                    onChange={handleFileChange}
                                />
                                <button
                                    className="icon-btn"
                                    title="Attach file"
                                    onClick={() => fileRef.current?.click()}
                                >
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
