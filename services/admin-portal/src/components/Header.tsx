import { Gauge, Moon, PanelLeftOpen, Sun } from 'lucide-react';
import { routeHref } from '../hooks/useHashRoute';
import type { Theme } from '../hooks/useTheme';

interface Props {
  title: string;
  subtitle?: string;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
  theme: Theme;
  onToggleTheme: () => void;
  /** Hide the dashboard shortcut while the dashboard page itself is open. */
  showLangfuse: boolean;
}

export default function Header({ title, subtitle, sidebarOpen, onToggleSidebar, theme, onToggleTheme, showLangfuse }: Props) {
  return (
    <header className="header">
      <div className="header__left">
        {!sidebarOpen && (
          <button className="icon-btn" onClick={onToggleSidebar} title="Open sidebar" aria-label="Open sidebar">
            <PanelLeftOpen size={20} />
          </button>
        )}
        <div className="header__titles">
          <h1 className="header__title">{title}</h1>
          {subtitle && <div className="header__subtitle">{subtitle}</div>}
        </div>
      </div>
      <div className="header__right">
        {showLangfuse && (
          <a className="btn btn--sm header__langfuse" href={routeHref({ page: 'langfuse' })}>
            <Gauge size={14} /> Langfuse dashboard
          </a>
        )}
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
