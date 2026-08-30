import { useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { copyText } from '../utils/clipboard';

interface Props {
  text: string;
  label?: string;
  className?: string;
}

export default function CopyButton({ text, label, className = 'action-btn' }: Props) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const onClick = async () => {
    const ok = await copyText(text);
    if (!ok) return;
    setCopied(true);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <button className={className} onClick={onClick} title={copied ? 'Copied' : 'Copy'} aria-label="Copy">
      {copied ? <Check size={16} /> : <Copy size={16} />}
      {label && <span>{copied ? 'Copied' : label}</span>}
    </button>
  );
}
