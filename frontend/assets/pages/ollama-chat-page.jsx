window.BELGIUM = window.BELGIUM || {};

const OLLAMA_MODELS = ["qwen2.5vl:latest", "llama3.2:latest", "mistral:latest", "gemma2:latest"];

function formatChatTime(date) {
  try {
    return new Date(date).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch (_) {
    return "";
  }
}

BELGIUM.OllamaChatPage = function OllamaChatPage() {
  const [conversations, setConversations] = React.useState([]);
  const [activeId, setActiveId] = React.useState(null);
  const [search, setSearch] = React.useState("");
  const [model, setModel] = React.useState(OLLAMA_MODELS[0]);
  const [draft, setDraft] = React.useState("");
  const backendReady = false;

  const activeConversation = conversations.find((item) => item.id === activeId) || null;

  const filteredConversations = React.useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return conversations;
    return conversations.filter((item) => item.title.toLowerCase().includes(query));
  }, [conversations, search]);

  function createConversation() {
    const id = `chat-${Date.now()}`;
    const conversation = {
      id,
      title: "New conversation",
      createdAt: new Date().toISOString(),
      messages: [],
    };
    setConversations((prev) => [conversation, ...prev]);
    setActiveId(id);
    setDraft("");
  }

  function clearActiveConversation() {
    if (!activeId) return;
    setConversations((prev) =>
      prev.map((item) => (item.id === activeId ? { ...item, messages: [] } : item))
    );
    setDraft("");
  }

  function handleSend() {
    if (!backendReady) return;
  }

  return (
    <div className="app-page ollama-chat-page">
      <div className="ollama-chat-layout app-card rounded-xl">
        <aside className="ollama-chat-sidebar">
          <div className="ollama-chat-sidebar-head">
            <h1 className="ollama-chat-title">Ollama Chat</h1>
            <p className="ollama-chat-subtitle">Local vision and text assistant for belgium workflows</p>
            <button type="button" className="app-button app-button-primary ollama-new-chat-btn text-white" onClick={createConversation}>
              New Chat
            </button>
            <input
              type="search"
              className="app-input ollama-chat-search rounded-lg px-3 py-2 text-sm"
              placeholder="Search conversations"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          <div className="ollama-conversation-list">
            {filteredConversations.length ? (
              filteredConversations.map((conversation) => (
                <button
                  key={conversation.id}
                  type="button"
                  className={`ollama-conversation-item${conversation.id === activeId ? " is-active" : ""}`}
                  onClick={() => setActiveId(conversation.id)}
                >
                  <span className="ollama-conversation-title">{conversation.title}</span>
                  <span className="ollama-conversation-time">{formatChatTime(conversation.createdAt)}</span>
                </button>
              ))
            ) : (
              <p className="ollama-conversation-empty">No conversations yet.</p>
            )}
          </div>
        </aside>

        <main className="ollama-chat-main">
          <header className="ollama-chat-main-header">
            <div className="min-w-0">
              <h2 className="ollama-chat-main-title">
                {activeConversation ? activeConversation.title : "Select or start a chat"}
              </h2>
              {!backendReady && (
                <p className="ollama-chat-backend-note">Ollama chat backend is not configured yet.</p>
              )}
            </div>
            <div className="ollama-chat-header-actions">
              <label className="ollama-model-select-wrap">
                <span className="sr-only">Model</span>
                <select
                  className="app-input ollama-model-select rounded-lg px-3 py-2 text-sm"
                  value={model}
                  onChange={(event) => setModel(event.target.value)}
                  disabled={!backendReady}
                >
                  {OLLAMA_MODELS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="app-button app-btn-secondary ollama-clear-btn"
                onClick={clearActiveConversation}
                disabled={!activeConversation}
                title="Clear conversation"
              >
                <BELGIUM.Icon name="trash" size={14} />
              </button>
            </div>
          </header>

          <div className="ollama-chat-messages">
            {!activeConversation ? (
              <BELGIUM.EmptyState
                icon={<BELGIUM.Icon name="chat" size={28} />}
                title="No conversation selected"
                description="Create a new chat to get started. Messaging will be enabled once the Ollama backend is configured."
              />
            ) : activeConversation.messages.length ? (
              activeConversation.messages.map((message) => (
                <div
                  key={message.id}
                  className={`ollama-message ${message.role === "user" ? "is-user" : "is-assistant"}`}
                >
                  <div className="ollama-message-role">{message.role}</div>
                  <div className="ollama-message-body">{message.content}</div>
                </div>
              ))
            ) : (
              <BELGIUM.EmptyState
                title="Start a conversation"
                description="Ask about belgium processing, parcel workflows, or CAD output once the Ollama backend is configured."
              />
            )}
          </div>

          <footer className="ollama-chat-composer">
            <button
              type="button"
              className="app-button app-btn-secondary ollama-attach-btn"
              disabled
              title="Attachments will be available when chat backend is configured"
            >
              <BELGIUM.Icon name="paperclip" size={16} />
            </button>
            <input
              className="app-input ollama-chat-input rounded-lg px-3 py-2 text-sm"
              placeholder={backendReady ? "Ask about belgium processing…" : "Chat input disabled until backend is configured"}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={!backendReady || !activeConversation}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  handleSend();
                }
              }}
            />
            <button
              type="button"
              className="app-button app-button-primary ollama-send-btn text-white"
              onClick={handleSend}
              disabled={!backendReady || !activeConversation || !draft.trim()}
            >
              <BELGIUM.Icon name="send" size={16} />
              Send
            </button>
          </footer>
        </main>
      </div>
    </div>
  );
};
