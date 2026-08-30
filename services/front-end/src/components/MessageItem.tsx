import { CircleAlert, RefreshCw } from 'lucide-react';
import CopyButton from './CopyButton';
import Markdown from './Markdown';
import type { Message } from '../types';

interface Props {
  message: Message;
  isStreaming: boolean;
  isLast: boolean;
  canRegenerate: boolean;
  onRegenerate: () => void;
  onSuggestion: (text: string) => void;
}

export default function MessageItem({ message, isStreaming, isLast, canRegenerate, onRegenerate, onSuggestion }: Props) {
  if (message.role === 'user') {
    return (
      <div className="msg msg--user">
        <div className="msg__bubble">{message.content}</div>
        <div className="msg__actions">
          <CopyButton text={message.content} />
        </div>
      </div>
    );
  }

  const showActions = !isStreaming && message.content.length > 0;

  return (
    <div className="msg msg--assistant">
      {message.content ? (
        <Markdown content={message.content} />
      ) : isStreaming ? (
        <div className="typing" aria-label="Assistant is typing">
          <span className="typing__dot" />
        </div>
      ) : null}

      {message.error && (
        <div className="msg__error" role="alert">
          <CircleAlert size={18} />
          <span className="msg__error-text">{message.error}</span>
          {canRegenerate && (
            <button className="btn btn--sm" onClick={onRegenerate}>
              Retry
            </button>
          )}
        </div>
      )}

      {showActions && (
        <div className={`msg__actions${isLast ? ' msg__actions--visible' : ''}`}>
          {message.content && <CopyButton text={message.content} />}
          {canRegenerate && (
            <button className="action-btn" onClick={onRegenerate} title="Regenerate" aria-label="Regenerate response">
              <RefreshCw size={16} />
            </button>
          )}
        </div>
      )}

      {/* Follow-up suggestions: only on the latest finished answer. */}
      {isLast && !isStreaming && !message.error && !!message.suggestions?.length && (
        <div className="msg__suggestions" aria-label="Suggested follow-ups">
          {message.suggestions.map((text) => (
            <button key={text} className="suggestion-chip" onClick={() => onSuggestion(text)}>
              {text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
