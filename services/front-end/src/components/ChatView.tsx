import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { ArrowDown } from 'lucide-react';
import MessageItem from './MessageItem';
import type { Conversation } from '../types';

interface Props {
  conversation: Conversation;
  streamingMessageId: string | null;
  onRegenerate: (messageId: string) => void;
  onSuggestion: (text: string) => void;
}

const NEAR_BOTTOM_PX = 80;

export default function ChatView({ conversation, streamingMessageId, onRegenerate, onSuggestion }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, []);

  // New conversation opened: jump to the end and re-enable auto-follow.
  useLayoutEffect(() => {
    stickToBottom.current = true;
    scrollToBottom();
  }, [conversation.id, scrollToBottom]);

  // Messages changed (new turn or streamed chunk): follow if the user hasn't scrolled up.
  useLayoutEffect(() => {
    if (stickToBottom.current) scrollToBottom();
  }, [conversation.messages, scrollToBottom]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const near = distance < NEAR_BOTTOM_PX;
    stickToBottom.current = near;
    setShowJump(!near);
  };

  const lastIndex = conversation.messages.length - 1;

  return (
    <div className="chat-area">
      <div className="chat" ref={scrollRef} onScroll={onScroll}>
        <div className="chat__inner">
          {conversation.messages.map((message, index) => (
            <MessageItem
              key={message.id}
              message={message}
              isStreaming={message.id === streamingMessageId}
              isLast={index === lastIndex}
              canRegenerate={index > 0 && conversation.messages[index - 1].role === 'user'}
              onRegenerate={() => onRegenerate(message.id)}
              onSuggestion={onSuggestion}
            />
          ))}
        </div>
      </div>
      {showJump && (
        <button
          className="scroll-btn"
          onClick={() => {
            stickToBottom.current = true;
            scrollToBottom('smooth');
          }}
          aria-label="Scroll to bottom"
          title="Scroll to bottom"
        >
          <ArrowDown size={18} />
        </button>
      )}
    </div>
  );
}
