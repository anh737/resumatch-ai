import type { UploadStatus } from '../api/admin';

const LABEL: Record<UploadStatus, string> = {
  queued: 'Queued',
  processing: 'Processing',
  done: 'Done',
  failed: 'Failed',
};

interface Props {
  status: UploadStatus;
  title?: string;
}

export default function StatusBadge({ status, title }: Props) {
  return (
    <span className={`status status--${status}`} title={title}>
      {LABEL[status]}
    </span>
  );
}
