import { Moon, PanelLeftOpen, SquarePen, Sun } from 'lucide-react';
import type { Theme } from '../hooks/useTheme';

interface Props {
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  onNewChat: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

export default function Header({ sidebarOpen, onToggleSidebar, onNewChat, theme, onToggleTheme }: Props) {
  return (
    <header className="header">
      <div className="header__left">
        {!sidebarOpen && (
          <>
            <button className="icon-btn" onClick={onToggleSidebar} title="Open sidebar" aria-label="Open sidebar">
              <PanelLeftOpen size={20} />
            </button>
            <button className="icon-btn" onClick={onNewChat} title="New chat" aria-label="New chat">
              <SquarePen size={20} />
            </button>
          </>
        )}
        <div className="header__title">Resume Scan AI</div>
      </div>
      <div className="header__right">
        <button
          className="icon-btn"
          onClick={onToggleTheme}
          title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
        </button>
      </div>
    </header>
  );
}
