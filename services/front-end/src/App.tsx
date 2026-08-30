import { useCallback, useEffect, useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import ChatView from './components/ChatView';
import Composer from './components/Composer';
import EmptyState from './components/EmptyState';
import { useConversations } from './store/useConversations';
import { useTheme } from './hooks/useTheme';
import { streamChat } from './api/chat';
import { uid } from './utils/id';
import type { Message } from './types';

const SIDEBAR_KEY = 'rs.sidebar';
const MOBILE_QUERY = '(max-width: 768px)';

function isMobile(): boolean {
  return window.matchMedia(MOBILE_QUERY).matches;
}

function initialSidebarOpen(): boolean {
  if (isMobile()) return false;
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== 'closed';
  } catch {
    return true;
  }
}

function titleFrom(text: string): string {
  const oneLine = text.replace(/\s+/g, ' ').trim();
  return oneLine.length > 40 ? `${oneLine.slice(0, 40).trimEnd()}…` : oneLine;
}

interface StreamingState {
  conversationId: string;
  messageId: string;
}

export default function App() {
  const { theme, toggle: toggleTheme } = useTheme();
  const {
    conversations,
    active,
    activeId,
    select,
    create,
    rename,
    remove,
    appendMessage,
    updateMessage,
    truncateFrom,
    removeIfEmpty,
  } = useConversations();

  const [sidebarOpen, setSidebarOpen] = useState(initialSidebarOpen);
  const [streaming, setStreaming] = useState<StreamingState | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (isMobile()) return;
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? 'open' : 'closed');
    } catch {
      // ignore
    }
  }, [sidebarOpen]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const runAssistant = useCallback(
    async (conversationId: string, history: Message[]) => {
      const assistantId = uid();
      appendMessage(conversationId, { id: assistantId, role: 'assistant', content: '', createdAt: Date.now() });

      const controller = new AbortController();
      abortRef.current = controller;
      setStreaming({ conversationId, messageId: assistantId });

      // Tokens are batched per animation frame so the markdown renderer is not
      // re-run for every few characters the server sends.
      let pending = '';
      let frame: number | null = null;
      const flush = () => {
        frame = null;
        if (!pending) return;
        const chunk = pending;
        pending = '';
        updateMessage(conversationId, assistantId, (m) => ({ ...m, content: m.content + chunk }));
      };
      const onToken = (text: string) => {
        pending += text;
        if (frame === null) frame = requestAnimationFrame(flush);
      };

      try {
        const result = await streamChat(
          {
            conversation_id: conversationId,
            messages: history.map(({ role, content }) => ({ role, content })),
          },
          onToken,
          controller.signal,
        );
        if (result.suggestions?.length) {
          const suggestions = result.suggestions;
          updateMessage(conversationId, assistantId, (m) => ({ ...m, suggestions }));
        }
      } catch (err) {
        if (!(err instanceof Error && err.name === 'AbortError')) {
          const message = err instanceof Error ? err.message : String(err);
          updateMessage(conversationId, assistantId, (m) => ({ ...m, error: message }));
        }
      } finally {
        if (frame !== null) cancelAnimationFrame(frame);
        flush();
        abortRef.current = null;
        setStreaming(null);
        removeIfEmpty(conversationId, assistantId);
      }
    },
    [appendMessage, updateMessage, removeIfEmpty],
  );

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;

      const existing = conversations.find((c) => c.id === activeId);
      const conversationId = existing ? existing.id : create().id;
      const history = existing ? existing.messages : [];

      const userMessage: Message = { id: uid(), role: 'user', content: trimmed, createdAt: Date.now() };
      appendMessage(conversationId, userMessage);
      if (history.length === 0) rename(conversationId, titleFrom(trimmed));

      void runAssistant(conversationId, [...history, userMessage]);
    },
    [streaming, conversations, activeId, create, appendMessage, rename, runAssistant],
  );

  const regenerate = useCallback(
    (conversationId: string, messageId: string) => {
      if (streaming) return;
      const conversation = conversations.find((c) => c.id === conversationId);
      if (!conversation) return;
      const index = conversation.messages.findIndex((m) => m.id === messageId);
      if (index === -1) return;
      const history = conversation.messages.slice(0, index);
      if (history.length === 0 || history[history.length - 1].role !== 'user') return;

      truncateFrom(conversationId, index);
      void runAssistant(conversationId, history);
    },
    [streaming, conversations, truncateFrom, runAssistant],
  );

  const newChat = useCallback(() => {
    select(null);
    if (isMobile()) setSidebarOpen(false);
  }, [select]);

  const selectConversation = useCallback(
    (id: string) => {
      select(id);
      if (isMobile()) setSidebarOpen(false);
    },
    [select],
  );

  const deleteConversation = useCallback(
    (id: string) => {
      if (streaming?.conversationId === id) stop();
      remove(id);
    },
    [streaming, stop, remove],
  );

  const hasMessages = !!active && active.messages.length > 0;
  const streamingHere = !!streaming && streaming.conversationId === active?.id;
  const busyElsewhere = !!streaming && !streamingHere;

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
        onClose={() => setSidebarOpen(false)}
        onNew={newChat}
        onSelect={selectConversation}
        onRename={rename}
        onDelete={deleteConversation}
      />

      <div className="main">
        <Header
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((o) => !o)}
          onNewChat={newChat}
          theme={theme}
          onToggleTheme={toggleTheme}
        />

        {hasMessages ? (
          <>
            <ChatView
              conversation={active}
              streamingMessageId={streamingHere ? streaming.messageId : null}
              onRegenerate={(messageId) => regenerate(active.id, messageId)}
              onSuggestion={send}
            />
            <Composer onSend={send} onStop={stop} streaming={streamingHere} disabled={busyElsewhere} showNote />
          </>
        ) : (
          <EmptyState onSend={send} onStop={stop} streaming={streamingHere} disabled={busyElsewhere} />
        )}
      </div>
    </div>
  );
}
