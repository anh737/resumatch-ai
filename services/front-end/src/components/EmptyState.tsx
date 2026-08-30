import { useRef } from 'react';
import { FileSearch, FileText, ListChecks, Users } from 'lucide-react';
import Composer, { type ComposerHandle } from './Composer';

interface Props {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
}

const SUGGESTIONS = [
  {
    icon: FileSearch,
    label: 'Screen a resume',
    prompt:
      'Screen the following resume against this job description. Give a fit score (0–100), key strengths, gaps, and a hiring recommendation.\n\nJob description:\n\n\nResume:\n',
  },
  {
    icon: FileText,
    label: 'Summarize a candidate',
    prompt:
      "Summarize this candidate's profile in 5 bullet points: experience, skills, education, notable achievements, and any red flags.\n\n",
  },
  {
    icon: ListChecks,
    label: 'Draft interview questions',
    prompt: 'Based on this resume and the role, draft 8 targeted interview questions (mix technical and behavioral).\n\n',
  },
  {
    icon: Users,
    label: 'Compare candidates',
    prompt: 'Compare these candidates for the role and rank them with reasoning for each placement.\n\n',
  },
];

export default function EmptyState({ onSend, onStop, streaming, disabled }: Props) {
  const composerRef = useRef<ComposerHandle>(null);

  return (
    <div className="empty">
      <h1 className="empty__title">What can I help with?</h1>
      <Composer ref={composerRef} onSend={onSend} onStop={onStop} streaming={streaming} disabled={disabled} />
      <div className="suggestions">
        {SUGGESTIONS.map(({ icon: Icon, label, prompt }) => (
          <button key={label} className="chip" onClick={() => composerRef.current?.insert(prompt)}>
            <Icon size={16} />
            <span>{label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
