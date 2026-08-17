import React, { useEffect, useState } from "react";
import {
  deleteConversation,
  getConversation,
  listConversations,
  streamChat,
  undoDisposition
} from "../api";
import { useAuth } from "../context/AuthContext";

export default function Chat() {
  const { setStatus } = useAuth();
  const [chatMessage, setChatMessage] = useState("");
  const [transcript, setTranscript] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [streaming, setStreaming] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  async function openConversation(id) {
    try {
      const conversation = await getConversation(id);
      setConversationId(conversation.id);
      setTranscript(
        (conversation.messages || []).map((message) => ({
          role: message.role,
          content: message.content,
          actions: []
        }))
      );
      setHistoryOpen(false);
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function loadConversations(preferredId) {
    try {
      const rows = await listConversations();
      setConversations(rows);
      const openId = preferredId || conversationId || rows[0]?.id;
      if (openId && rows.some((row) => row.id === openId)) {
        if (openId !== conversationId || transcript.length === 0) {
          await openConversation(openId);
        }
      }
    } catch (err) {
      setStatus(err.message);
    }
  }

  useEffect(() => {
    loadConversations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleNewConversation = () => {
    setConversationId(null);
    setTranscript([]);
    setStatus("");
    setHistoryOpen(false);
  };

  const handleDeleteConversation = async (id) => {
    try {
      await deleteConversation(id);
      const remaining = conversations.filter((row) => row.id !== id);
      setConversations(remaining);
      if (conversationId === id) {
        if (remaining[0]) {
          await openConversation(remaining[0].id);
        } else {
          handleNewConversation();
        }
      }
    } catch (err) {
      setStatus(err.message);
    }
  };

  const handleChat = async () => {
    if (!chatMessage || streaming) return;

    const question = chatMessage;
    setChatMessage("");
    setStreaming(true);
    setStatus("");
    setTranscript((turns) => [
      ...turns,
      { role: "user", content: question },
      { role: "assistant", content: "", actions: [] }
    ]);

    const updateReply = (change) =>
      setTranscript((turns) => {
        const next = [...turns];
        next[next.length - 1] = change(next[next.length - 1]);
        return next;
      });

    try {
      await streamChat(question, conversationId, (event) => {
        if (event.type === "token") {
          updateReply((turn) => ({
            ...turn,
            content: turn.content + event.text
          }));
        } else if (event.type === "action") {
          updateReply((turn) => ({
            ...turn,
            actions: [...turn.actions, event]
          }));
        } else if (event.type === "done") {
          setConversationId(event.conversation_id);
          listConversations()
            .then(setConversations)
            .catch(() => {});
        } else if (event.type === "error") {
          setStatus(event.detail);
        }
      });
    } catch (err) {
      setStatus(err.message);
    } finally {
      setStreaming(false);
    }
  };

  const handleUndo = async (turnIndex, actionIndex) => {
    const action = transcript[turnIndex].actions[actionIndex];
    try {
      await undoDisposition(action.undo.item_id, action.undo.disposition_id);
      setTranscript((turns) =>
        turns.map((turn, i) =>
          i !== turnIndex
            ? turn
            : {
                ...turn,
                actions: turn.actions.map((entry, j) =>
                  j === actionIndex ? { ...entry, undone: true } : entry
                )
              }
        )
      );
    } catch (err) {
      setStatus(err.message);
    }
  };

  const historyPane = (
    <aside className="chat-history">
      <button type="button" onClick={handleNewConversation}>
        New chat
      </button>
      <ul className="conversation-list">
        {conversations.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              className={
                row.id === conversationId
                  ? "conversation-item active"
                  : "conversation-item"
              }
              onClick={() => openConversation(row.id)}
            >
              {row.title}
            </button>
            <button
              type="button"
              className="link-button"
              onClick={() => handleDeleteConversation(row.id)}
            >
              Delete
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );

  return (
    <div className="page chat-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Inventory-grounded assistant</p>
          <h1>Chat</h1>
        </div>
        <button
          type="button"
          className="ghost-button history-toggle"
          onClick={() => setHistoryOpen((open) => !open)}
        >
          {historyOpen ? "Hide history" : "History"}
        </button>
      </header>

      <div className="chat-layout">
        <div className="chat-history-desktop">{historyPane}</div>
        {historyOpen ? (
          <div className="chat-history-drawer">{historyPane}</div>
        ) : null}

        <section className="card chat-thread">
          <p className="hint">
            It can see what is expiring, and can record what you have used or
            thrown out.
          </p>

          {transcript.length > 0 ? (
            <ol className="chat-transcript">
              {transcript.map((turn, index) => (
                <li key={index} className={`chat-turn chat-turn-${turn.role}`}>
                  <p className="chat-content">
                    {turn.content}
                    {turn.role === "assistant" &&
                      streaming &&
                      index === transcript.length - 1 && (
                        <span className="chat-caret" />
                      )}
                  </p>
                  {turn.actions?.map((action, actionIndex) => (
                    <p
                      key={actionIndex}
                      className={`chat-action ${action.ok ? "ok" : "failed"}`}
                    >
                      {action.summary}
                      {action.undo && !action.undone && (
                        <button
                          type="button"
                          className="link-button undo-button"
                          onClick={() => handleUndo(index, actionIndex)}
                        >
                          Undo
                        </button>
                      )}
                      {action.undone && (
                        <span className="undone"> — undone</span>
                      )}
                    </p>
                  ))}
                </li>
              ))}
            </ol>
          ) : (
            <p className="hint">Ask what to cook, or say what you used up.</p>
          )}

          <div className="chat-composer">
            <textarea
              rows={3}
              placeholder="Ask about your fridge..."
              value={chatMessage}
              onChange={(event) => setChatMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  handleChat();
                }
              }}
            />
            <button type="button" disabled={streaming} onClick={handleChat}>
              {streaming ? "Thinking..." : "Send"}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
