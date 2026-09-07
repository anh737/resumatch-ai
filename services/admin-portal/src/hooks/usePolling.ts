import { useEffect, useRef } from 'react';

/** Run `callback` every `intervalMs` while `enabled` (latest callback, no re-subscribe on change). */
export function usePolling(callback: () => void | Promise<void>, intervalMs: number, enabled = true): void {
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  });
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => void saved.current(), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs, enabled]);
}
