import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { Ellipsis, Pencil, Trash2 } from 'lucide-react';
import ConfirmDialog from './ConfirmDialog';
import type { Conversation } from '../types';

interface Props {
  conversation: Conversation;
  active: boolean;
  onSelect: () => void;
  onRename: (title: string) => void;
  onDelete: () => void;
}

interface MenuPosition {
  top: number;
  left: number;
}

export default function ConversationItem({ conversation, active, onSelect, onRename, onDelete }: Props) {
  const [menu, setMenu] = useState<MenuPosition | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(conversation.title);
  const [confirming, setConfirming] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (e: globalThis.MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(null);
    };
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') setMenu(null);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  const openMenu = (e: MouseEvent<HTMLButtonElement>) => {
    e.stopPropagation();
    const rect = e.currentTarget.getBoundingClientRect();
    setMenu({ top: rect.bottom + 4, left: rect.left });
  };

  const startRename = () => {
    setMenu(null);
    setDraft(conversation.title);
    setEditing(true);
  };

  const commitRename = () => {
    const title = draft.trim();
    if (title && title !== conversation.title) onRename(title);
    setEditing(false);
  };

  const onInputKey = (e: KeyboardEvent<HTMLInputElement>) => {
    e.stopPropagation();
    if (e.key === 'Enter') commitRename();
    if (e.key === 'Escape') setEditing(false);
  };

  const className = ['conv', active ? 'conv--active' : '', menu ? 'conv--menu-open' : ''].filter(Boolean).join(' ');

  return (
    <div
      className={className}
      role="button"
      tabIndex={0}
      onClick={() => {
        if (!editing) onSelect();
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && !editing) onSelect();
      }}
    >
      {editing ? (
        <input
          className="conv__input"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={onInputKey}
          onClick={(e) => e.stopPropagation()}
          aria-label="Rename chat"
        />
      ) : (
        <span className="conv__title" title={conversation.title}>
          {conversation.title}
        </span>
      )}

      <button className="conv__menu-btn icon-btn icon-btn--sm" onClick={openMenu} aria-label="Chat options">
        <Ellipsis size={16} />
      </button>

      {menu &&
        createPortal(
          <div
            ref={menuRef}
            className="menu"
            style={{ top: menu.top, left: menu.left }}
            role="menu"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="menu__item" role="menuitem" onClick={startRename}>
              <Pencil size={16} />
              <span>Rename</span>
            </button>
            <button
              className="menu__item menu__item--danger"
              role="menuitem"
              onClick={() => {
                setMenu(null);
                setConfirming(true);
              }}
            >
              <Trash2 size={16} />
              <span>Delete</span>
            </button>
          </div>,
          document.body,
        )}

      {confirming && (
        <ConfirmDialog
          title="Delete chat?"
          description={`This will delete "${conversation.title}". This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={() => {
            setConfirming(false);
            onDelete();
          }}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
