export type Role = 'user' | 'assistant';

export interface Message {
  id: string;
  role: Role;
  content: string;
  createdAt: number;
  /** Follow-up prompts suggested by the assistant for this turn. */
  suggestions?: string[];
  /** Set when the request for this assistant turn failed. */
  error?: string;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}
