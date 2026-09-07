import { Activity, Briefcase, ExternalLink, FileText, Gauge, LayoutDashboard, MessageSquare, PanelLeftClose } from 'lucide-react';
import { routeHref, type Route } from '../hooks/useHashRoute';

interface Props {
  route: Route;
  open: boolean;
  /** Close button + backdrop. */
  onClose: () => void;
  /** After a nav link was followed (the app closes the sidebar on mobile only). */
  onNavigate: () => void;
  chatUrl?: string;
}

interface NavItem {
  route: Route;
  label: string;
  icon: typeof FileText;
  description: string;
}

const NAV: NavItem[] = [
  { route: { page: 'overview' }, label: 'Overview', icon: LayoutDashboard, description: 'Pipeline status' },
  { route: { page: 'upload', kind: 'cv' }, label: 'Upload CV', icon: FileText, description: 'Resumes → cv_information_technology' },
  { route: { page: 'upload', kind: 'jd' }, label: 'Upload JD', icon: Briefcase, description: 'Job descriptions → jd_jobs' },
  { route: { page: 'traces' }, label: 'Observability', icon: Activity, description: 'Traces, costs, observation trees' },
  { route: { page: 'langfuse' }, label: 'Langfuse dashboard', icon: Gauge, description: 'Full Langfuse UI, embedded' },
];

function isActive(item: Route, current: Route): boolean {
  if (item.page !== current.page) return false;
  if (item.page === 'upload' && current.page === 'upload') return item.kind === current.kind;
  return true;
}

export default function Sidebar({ route, open, onClose, onNavigate, chatUrl }: Props) {
  return (
    <>
      <div className={`sidebar-backdrop${open ? ' sidebar-backdrop--visible' : ''}`} onClick={onClose} aria-hidden />
      <aside className={`sidebar${open ? ' sidebar--open' : ' sidebar--closed'}`} aria-hidden={!open}>
        <div className="sidebar__inner">
          <div className="sidebar__top">
            <div className="sidebar__brand">
              <span className="sidebar__logo" aria-hidden>
                <svg viewBox="0 0 32 32" width="24" height="24">
                  <rect width="32" height="32" rx="8" fill="currentColor" />
                  <path d="M9 22.5 16 9l7 13.5" fill="none" stroke="var(--bg-sidebar)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                  <path d="M12 18.5h8" stroke="var(--bg-sidebar)" strokeWidth="2" strokeLinecap="round" />
                </svg>
              </span>
              <span className="sidebar__brand-text">
                <span className="sidebar__brand-name">Resume Scan</span>
                <span className="sidebar__brand-sub">Admin portal</span>
              </span>
            </div>
            <button className="icon-btn sidebar__close" onClick={onClose} title="Close sidebar" aria-label="Close sidebar">
              <PanelLeftClose size={20} />
            </button>
          </div>

          <nav className="sidebar__nav" aria-label="Sections">
            {NAV.map(({ route: target, label, icon: Icon, description }) => (
              <a
                key={label}
                href={routeHref(target)}
                className={`nav-item${isActive(target, route) ? ' nav-item--active' : ''}`}
                aria-current={isActive(target, route) ? 'page' : undefined}
                onClick={onNavigate}
              >
                <Icon size={18} />
                <span className="nav-item__text">
                  <span>{label}</span>
                  <span className="nav-item__desc">{description}</span>
                </span>
              </a>
            ))}
          </nav>

          {chatUrl && <div className="sidebar__section-label">Links</div>}
          <div className="sidebar__links">
            {chatUrl && (
              <a className="nav-item nav-item--ext" href={chatUrl} target="_blank" rel="noreferrer">
                <MessageSquare size={18} />
                <span>Chat UI</span>
                <ExternalLink size={14} className="nav-item__ext" />
              </a>
            )}
          </div>

          <div className="sidebar__bottom">
            <div className="user">
              <div className="user__avatar" aria-hidden>
                A
              </div>
              <div className="user__meta">
                <div className="user__name">Administrator</div>
                <div className="user__plan">Local workspace</div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
