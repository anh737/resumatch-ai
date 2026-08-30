import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Conversation, Message } from '../types';
import { uid } from '../utils/id';

const STORAGE_KEY = 'rs.chat.conversations.v1';
const ACTIVE_KEY = 'rs.chat.active.v1';

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (c): c is Conversation =>
        !!c && typeof c === 'object' && typeof (c as Conversation).id === 'string' && Array.isArray((c as Conversation).messages),
    );
  } catch {
    return [];
  }
}

function loadActiveId(conversations: Conversation[]): string | null {
  try {
    const id = localStorage.getItem(ACTIVE_KEY);
    return id && conversations.some((c) => c.id === id) ? id : null;
  } catch {
    return null;
  }
}

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(() => loadActiveId(loadConversations()));

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch {
      // quota exceeded or storage disabled — chat keeps working in memory
    }
  }, [conversations]);

  useEffect(() => {
    try {
      if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
      else localStorage.removeItem(ACTIVE_KEY);
    } catch {
      // ignore
    }
  }, [activeId]);

  const active = useMemo(() => conversations.find((c) => c.id === activeId) ?? null, [conversations, activeId]);

  const select = useCallback((id: string | null) => setActiveId(id), []);

  const create = useCallback((): Conversation => {
    const now = Date.now();
    const conversation: Conversation = { id: uid(), title: 'New chat', messages: [], createdAt: now, updatedAt: now };
    setConversations((prev) => [conversation, ...prev]);
    setActiveId(conversation.id);
    return conversation;
  }, []);

  const patch = useCallback((id: string, fn: (c: Conversation) => Conversation) => {
    setConversations((prev) => prev.map((c) => (c.id === id ? fn(c) : c)));
  }, []);

  const rename = useCallback((id: string, title: string) => patch(id, (c) => ({ ...c, title })), [patch]);

  const remove = useCallback((id: string) => {
    setConversations((prev) => prev.filter((c) => c.id !== id));
    setActiveId((current) => (current === id ? null : current));
  }, []);

  const appendMessage = useCallback(
    (id: string, message: Message) =>
      patch(id, (c) => ({ ...c, messages: [...c.messages, message], updatedAt: Date.now() })),
    [patch],
  );

  const updateMessage = useCallback(
    (id: string, messageId: string, fn: (m: Message) => Message) =>
      patch(id, (c) => ({ ...c, messages: c.messages.map((m) => (m.id === messageId ? fn(m) : m)) })),
    [patch],
  );

  /** Drops the message at `index` and everything after it (used before regenerating). */
  const truncateFrom = useCallback(
    (id: string, index: number) => patch(id, (c) => ({ ...c, messages: c.messages.slice(0, index) })),
    [patch],
  );

  /** Removes an assistant placeholder that never received content (e.g. stopped immediately). */
  const removeIfEmpty = useCallback(
    (id: string, messageId: string) =>
      patch(id, (c) => ({
        ...c,
        messages: c.messages.filter((m) => !(m.id === messageId && m.content === '' && !m.error)),
      })),
    [patch],
  );

  return {
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
  };
}
