import { useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Upload } from 'lucide-react';

interface Props {
  accept: string;
  multiple?: boolean;
  disabled?: boolean;
  title: string;
  hint: string;
  icon: ReactNode;
  onFiles: (files: File[]) => void;
}

/** Click-or-drop file picker. Keyboard: Enter / Space opens the native dialog. */
export default function Dropzone({ accept, multiple = true, disabled = false, title, hint, icon, onFiles }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const depth = useRef(0); // dragenter/dragleave fire for every child; count them
  const [dragging, setDragging] = useState(false);

  const open = () => {
    if (!disabled) inputRef.current?.click();
  };

  const accepted = (list: FileList | null | undefined): File[] => {
    const files = Array.from(list ?? []);
    return multiple ? files : files.slice(0, 1);
  };

  const onDragEnter = (e: DragEvent) => {
    e.preventDefault();
    depth.current += 1;
    if (!disabled) setDragging(true);
  };
  const onDragLeave = (e: DragEvent) => {
    e.preventDefault();
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setDragging(false);
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    depth.current = 0;
    setDragging(false);
    if (disabled) return;
    const files = accepted(e.dataTransfer.files);
    if (files.length) onFiles(files);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      open();
    }
  };

  return (
    <div
      className={`dropzone${dragging ? ' dropzone--dragging' : ''}${disabled ? ' dropzone--disabled' : ''}`}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled}
      onClick={open}
      onKeyDown={onKeyDown}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="dropzone__icon" aria-hidden>
        {icon}
      </div>
      <div className="dropzone__title">{title}</div>
      <div className="dropzone__hint">{hint}</div>
      <span className="btn btn--primary dropzone__cta">
        <Upload size={16} />
        {multiple ? 'Choose files' : 'Choose file'}
      </span>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => {
          const files = accepted(e.target.files);
          e.target.value = '';
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}
