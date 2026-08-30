/**
 * Chat API client — WebSocket streaming via the bot-agent / ai-agents pipeline.
 *
 * One turn works in two steps:
 *   1. open  WS  {VITE_API_BASE_URL}/ws/chat/{conversation_id}   (ai-agents)
 *   2. POST      {VITE_API_BASE_URL}/chat                        (bot-agent)
 *           { "conversation_id": string, "message": string }  ->  202 {status:"queued"}
 *
 * bot-agent persists the message and forwards it (with the last 10 turns of
 * history) to ai-agents over Kafka; ai-agents streams the answer back on the
 * WebSocket as JSON events, one per frame:
 *   {"type":"tool_call","tool":"retrieval_cv","arguments":{...}}   // progress, ignored here
 *   {"content":"token"}                                            // answer text, repeated
 *   {"type":"suggestions","suggestions":["...", ...]}              // follow-up prompts
 *   {"type":"done"} | {"type":"error","message":"..."}
 *
 * The socket is opened before the POST so no token can be missed.
 */
import type { Role } from '../types';
import { uid } from '../utils/id';

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/+$/, '');

/** Abort the turn when the socket has been silent for this long after the POST. */
const IDLE_TIMEOUT_MS = 120_000;

export interface ChatMessagePayload {
  role: Role;
  content: string;
}

export interface ChatRequest {
  conversation_id: string | null;
  messages: ChatMessagePayload[];
}

export interface ChatResult {
  conversationId?: string;
  /** Follow-up prompts the assistant suggests for the next turn. */
  suggestions?: string[];
}

export class ChatApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ChatApiError';
    this.status = status;
  }
}

export type TokenHandler = (text: string) => void;

export async function streamChat(
  request: ChatRequest,
  onToken: TokenHandler,
  signal?: AbortSignal,
): Promise<ChatResult> {
  const conversationId = request.conversation_id ?? uid();
  const message = [...request.messages].reverse().find((m) => m.role === 'user' && m.content.trim());
  if (!message) throw new ChatApiError('There is no user message to send.');
  if (signal?.aborted) throw abortError();

  const socket = await openSocket(wsUrl(`/ws/chat/${encodeURIComponent(conversationId)}`), signal);

  try {
    const answered = waitForAnswer(socket, onToken, signal);
    await postTurn(conversationId, message.content, signal);
    const { suggestions } = await answered;
    return { conversationId, suggestions };
  } finally {
    closeQuietly(socket);
  }
}

function wsUrl(path: string): string {
  const base = /^https?:/i.test(API_BASE) ? API_BASE : `${window.location.origin}${API_BASE}`;
  return base.replace(/^http/i, 'ws') + path;
}

function abortError(): Error {
  return new DOMException('The chat request was aborted.', 'AbortError');
}

function closeQuietly(socket: WebSocket): void {
  socket.onmessage = socket.onclose = socket.onerror = null;
  try {
    socket.close();
  } catch {
    // already closed
  }
}

function openSocket(url: string, signal?: AbortSignal): Promise<WebSocket> {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    const onAbort = () => {
      closeQuietly(socket);
      reject(abortError());
    };
    signal?.addEventListener('abort', onAbort, { once: true });
    socket.onopen = () => {
      signal?.removeEventListener('abort', onAbort);
      resolve(socket);
    };
    socket.onerror = () => {
      signal?.removeEventListener('abort', onAbort);
      reject(new ChatApiError(`Cannot open the answer stream at ${url}. Is the ai-agents service running?`));
    };
  });
}

async function postTurn(conversationId: string, message: string, signal?: AbortSignal): Promise<void> {
  const url = `${API_BASE}/chat`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, message }),
      signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') throw err;
    throw new ChatApiError(`Cannot reach the chat API at ${url}. Is the bot-agent service running?`);
  }
  if (!response.ok) {
    throw new ChatApiError(await readErrorBody(response), response.status);
  }
}

/** Resolves on {"type":"done"}, rejects on error / abort / silent socket. */
function waitForAnswer(
  socket: WebSocket,
  onToken: TokenHandler,
  signal?: AbortSignal,
): Promise<{ suggestions?: string[] }> {
  return new Promise((resolve, reject) => {
    let suggestions: string[] | undefined;
    let idleTimer: number | undefined;
    const cleanup = () => {
      if (idleTimer !== undefined) window.clearTimeout(idleTimer);
      signal?.removeEventListener('abort', onAbort);
    };
    const fail = (err: Error) => {
      cleanup();
      reject(err);
    };
    const onAbort = () => fail(abortError());
    const restartIdleTimer = () => {
      if (idleTimer !== undefined) window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(
        () => fail(new ChatApiError('The assistant stopped responding. Please try again.')),
        IDLE_TIMEOUT_MS,
      );
    };

    signal?.addEventListener('abort', onAbort, { once: true });
    restartIdleTimer();

    socket.onclose = () => fail(new ChatApiError('The answer stream closed before the reply finished.'));
    socket.onerror = () => fail(new ChatApiError('The answer stream failed.'));
    socket.onmessage = (event: MessageEvent<string>) => {
      restartIdleTimer();
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return; // ignore non-JSON frames
      }
      if (data.type === 'error') {
        const detail = data.message ?? data.detail;
        fail(new ChatApiError(typeof detail === 'string' ? detail : 'The chat API returned an error.'));
      } else if (data.type === 'done') {
        cleanup();
        resolve({ suggestions });
      } else if (data.type === 'suggestions' && Array.isArray(data.suggestions)) {
        suggestions = data.suggestions.filter((s): s is string => typeof s === 'string' && s.length > 0);
      } else if (typeof data.content === 'string' && data.content) {
        onToken(data.content);
      }
      // other event types (e.g. tool_call progress) are ignored for now
    };
  });
}

async function readErrorBody(response: Response): Promise<string> {
  const fallback = `The chat API responded with ${response.status} ${response.statusText}`.trim();
  try {
    const text = await response.text();
    if (!text) return fallback;
    try {
      const data = JSON.parse(text) as Record<string, unknown>;
      const detail = data.detail ?? data.message ?? data.error;
      if (typeof detail === 'string') return detail;
      if (detail !== undefined) return JSON.stringify(detail);
    } catch {
      // not JSON
    }
    return text.length > 300 ? `${text.slice(0, 300)}…` : text;
  } catch {
    return fallback;
  }
}
