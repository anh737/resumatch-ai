import { useMemo, useState } from 'react';
import { ExternalLink, PanelLeftClose, Search, ShieldCheck, SquarePen, X } from 'lucide-react';
import ConversationItem from './ConversationItem';
import type { Conversation } from '../types';
import { GROUP_ORDER, groupForDate, type DateGroup } from '../utils/dates';

// Separate admin app (services/admin-portal); the link is hidden when unset.
const ADMIN_PORTAL_URL = (import.meta.env.VITE_ADMIN_PORTAL_URL ?? '').trim();

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

interface Group {
  label: DateGroup;
  items: Conversation[];
}

function groupConversations(conversations: Conversation[]): Group[] {
  const now = Date.now();
  const buckets = new Map<DateGroup, Conversation[]>();
  for (const conversation of [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const label = groupForDate(conversation.updatedAt, now);
    const bucket = buckets.get(label);
    if (bucket) bucket.push(conversation);
    else buckets.set(label, [conversation]);
  }
  return GROUP_ORDER.filter((label) => buckets.has(label)).map((label) => ({ label, items: buckets.get(label)! }));
}

export default function Sidebar({
  conversations,
  activeId,
  open,
  onToggle,
  onClose,
  onNew,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState('');

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return conversations;
    return conversations.filter(
      (c) => c.title.toLowerCase().includes(q) || c.messages.some((m) => m.content.toLowerCase().includes(q)),
    );
  }, [conversations, query]);

  const groups = useMemo(() => groupConversations(filtered), [filtered]);

  const toggleSearch = () => {
    setSearching((s) => !s);
    setQuery('');
  };

  return (
    <>
      <div className={`sidebar-backdrop${open ? ' sidebar-backdrop--visible' : ''}`} onClick={onClose} aria-hidden />
      <aside className={`sidebar${open ? ' sidebar--open' : ' sidebar--closed'}`} aria-hidden={!open}>
        <div className="sidebar__inner">
          <div className="sidebar__top">
            <div className="sidebar__logo" aria-hidden>
              <svg viewBox="0 0 32 32" width="24" height="24">
                <rect width="32" height="32" rx="8" fill="currentColor" />
                <path
                  d="M10 9.5h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-6.5L11 24v-3.5h-1a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"
                  fill="none"
                  stroke="var(--bg-sidebar)"
                  strokeWidth="2"
                  strokeLinejoin="round"
                />
                <path d="M12 13.5h8M12 16.5h5" stroke="var(--bg-sidebar)" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </div>
            <button className="icon-btn" onClick={onToggle} title="Close sidebar" aria-label="Close sidebar">
              <PanelLeftClose size={20} />
            </button>
          </div>

          <nav className="sidebar__nav">
            <button className="nav-item" onClick={onNew}>
              <SquarePen size={18} />
              <span>New chat</span>
            </button>
            <button className={`nav-item${searching ? ' nav-item--active' : ''}`} onClick={toggleSearch}>
              <Search size={18} />
              <span>Search chats</span>
            </button>
            {ADMIN_PORTAL_URL && (
              <a className="nav-item nav-item--link" href={ADMIN_PORTAL_URL} target="_blank" rel="noreferrer">
                <ShieldCheck size={18} />
                <span>Admin portal</span>
                <ExternalLink size={14} className="nav-item__ext" />
              </a>
            )}
            {searching && (
              <div className="sidebar__search">
                <Search size={16} />
                <input
                  autoFocus
                  type="text"
                  placeholder="Search chats…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') toggleSearch();
                  }}
                />
                {query && (
                  <button className="icon-btn icon-btn--sm" onClick={() => setQuery('')} aria-label="Clear search">
                    <X size={14} />
                  </button>
                )}
              </div>
            )}
          </nav>

          <div className="sidebar__list">
            {groups.map((group) => (
              <div key={group.label} className="sidebar__group">
                <div className="sidebar__section">{group.label}</div>
                {group.items.map((conversation) => (
                  <ConversationItem
                    key={conversation.id}
                    conversation={conversation}
                    active={conversation.id === activeId}
                    onSelect={() => onSelect(conversation.id)}
                    onRename={(title) => onRename(conversation.id, title)}
                    onDelete={() => onDelete(conversation.id)}
                  />
                ))}
              </div>
            ))}
            {filtered.length === 0 && (
              <div className="sidebar__empty">{query ? 'No chats match your search.' : 'No chats yet.'}</div>
            )}
          </div>

          <div className="sidebar__bottom">
            <div className="user">
              <div className="user__avatar" aria-hidden>
                R
              </div>
              <div className="user__meta">
                <div className="user__name">Resume Scan</div>
                <div className="user__plan">Local workspace</div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
