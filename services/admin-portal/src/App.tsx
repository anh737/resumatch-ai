import { useCallback, useEffect, useState } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import TraceDrawer from './components/TraceDrawer';
import OverviewPage from './pages/OverviewPage';
import UploadPage from './pages/UploadPage';
import TracesPage from './pages/TracesPage';
import LangfusePage from './pages/LangfusePage';
import { listTraces } from './api/admin';
import { useHashRoute, type Route } from './hooks/useHashRoute';
import { useTheme } from './hooks/useTheme';

const SIDEBAR_KEY = 'rs-admin.sidebar';
const MOBILE_QUERY = '(max-width: 768px)';
const CHAT_URL = (import.meta.env.VITE_CHAT_URL ?? '').trim() || undefined;

function isMobile(): boolean {
  return window.matchMedia(MOBILE_QUERY).matches;
}

function initialSidebarOpen(): boolean {
  if (isMobile()) return false;
  try {
    return localStorage.getItem(SIDEBAR_KEY) !== 'closed';
  } catch {
    return true;
  }
}

function titles(route: Route): { title: string; subtitle: string } {
  switch (route.page) {
    case 'upload':
      return route.kind === 'cv'
        ? { title: 'Upload CV', subtitle: 'Resumes → MinIO → Kafka → ai-embeddings → Qdrant' }
        : { title: 'Upload JD', subtitle: 'Job descriptions → MinIO → Kafka → ai-embeddings → Qdrant' };
    case 'traces':
      return { title: 'Observability', subtitle: 'Langfuse traces of chat turns and ingestions' };
    case 'langfuse':
      return { title: 'Langfuse dashboard', subtitle: 'The Langfuse UI, embedded through the portal proxy' };
    default:
      return { title: 'Overview', subtitle: 'Ingestion pipeline and service health' };
  }
}

export default function App() {
  const { theme, toggle: toggleTheme } = useTheme();
  const [route, navigate] = useHashRoute();
  const [sidebarOpen, setSidebarOpen] = useState(initialSidebarOpen);
  const [langfuseUrl, setLangfuseUrl] = useState<string | null>(null);

  useEffect(() => {
    if (isMobile()) return;
    try {
      localStorage.setItem(SIDEBAR_KEY, sidebarOpen ? 'open' : 'closed');
    } catch {
      // ignore
    }
  }, [sidebarOpen]);

  // One cheap call to learn Langfuse's public URL (new-tab fallback of the
  // embedded dashboard); the pages fetch their own data.
  useEffect(() => {
    const controller = new AbortController();
    listTraces({ limit: 1, signal: controller.signal })
      .then((r) => setLangfuseUrl(r.configured && r.public_url ? r.public_url : null))
      .catch(() => setLangfuseUrl(null));
    return () => controller.abort();
  }, []);

  const openTrace = useCallback((traceId: string) => navigate({ page: 'traces', traceId }), [navigate]);
  const closeTrace = useCallback(() => navigate({ page: 'traces' }), [navigate]);
  const closeSidebar = useCallback(() => {
    if (isMobile()) setSidebarOpen(false);
  }, []);

  const { title, subtitle } = titles(route);
  const traceId = route.page === 'traces' ? route.traceId : undefined;

  useEffect(() => {
    document.title = `${title} · Resume Scan Admin`;
  }, [title]);

  return (
    <div className="app">
      <Sidebar
        route={route}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onNavigate={closeSidebar}
        chatUrl={CHAT_URL}
      />

      <div className="main">
        <Header
          title={title}
          subtitle={subtitle}
          sidebarOpen={sidebarOpen}
          onToggleSidebar={() => setSidebarOpen((o) => !o)}
          theme={theme}
          onToggleTheme={toggleTheme}
          showLangfuse={route.page !== 'langfuse'}
        />

        {route.page === 'overview' && <OverviewPage onOpenTrace={openTrace} />}
        {route.page === 'upload' && <UploadPage key={route.kind} kind={route.kind} onOpenTrace={openTrace} />}
        {route.page === 'traces' && <TracesPage onOpenTrace={openTrace} />}
        {route.page === 'langfuse' && <LangfusePage path={route.path} publicUrl={langfuseUrl} />}
      </div>

      {traceId && <TraceDrawer traceId={traceId} onClose={closeTrace} />}
    </div>
  );
}
