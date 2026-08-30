export type DateGroup = 'Today' | 'Yesterday' | 'Previous 7 Days' | 'Previous 30 Days' | 'Older';

export const GROUP_ORDER: DateGroup[] = ['Today', 'Yesterday', 'Previous 7 Days', 'Previous 30 Days', 'Older'];

const DAY_MS = 24 * 60 * 60 * 1000;

export function groupForDate(timestamp: number, now: number = Date.now()): DateGroup {
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const diff = startOfToday.getTime() - timestamp;
  if (diff <= 0) return 'Today';
  if (diff <= DAY_MS) return 'Yesterday';
  if (diff <= 7 * DAY_MS) return 'Previous 7 Days';
  if (diff <= 30 * DAY_MS) return 'Previous 30 Days';
  return 'Older';
}
