import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from 'react';
import { ArrowUp, Square } from 'lucide-react';

export interface ComposerHandle {
  /** Replaces the draft with `text` and focuses the input. */
  insert: (text: string) => void;
  focus: () => void;
}

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  showNote?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  ref?: Ref<ComposerHandle>;
}

const MAX_HEIGHT_PX = 200;

export default function Composer({
  onSend,
  onStop,
  streaming,
  disabled = false,
  showNote = false,
  placeholder = 'Ask anything',
  autoFocus = true,
  ref,
}: Props) {
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  useImperativeHandle(
    ref,
    () => ({
      insert: (text: string) => {
        setValue(text);
        requestAnimationFrame(() => {
          const el = textareaRef.current;
          if (!el) return;
          el.focus();
          el.setSelectionRange(el.value.length, el.value.length);
          el.scrollTop = el.scrollHeight;
        });
      },
      focus: () => textareaRef.current?.focus(),
    }),
    [],
  );

  const canSend = value.trim().length > 0 && !streaming && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(value);
    setValue('');
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    submit();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={onSubmit}>
        <textarea
          ref={textareaRef}
          className="composer__input"
          rows={1}
          value={value}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus={autoFocus}
          disabled={disabled}
          aria-label="Message"
        />
        {streaming ? (
          <button type="button" className="send-btn" onClick={onStop} title="Stop generating" aria-label="Stop generating">
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button type="submit" className="send-btn" disabled={!canSend} title="Send" aria-label="Send message">
            <ArrowUp size={18} strokeWidth={2.5} />
          </button>
        )}
      </form>
      {showNote && <p className="footer-note">Resume Scan AI can make mistakes. Check important info.</p>}
    </div>
  );
}
