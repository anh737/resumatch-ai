import type { ReactNode } from 'react';
import CopyButton from './CopyButton';

// Minimal structural view of the hast node react-markdown hands to `pre`.
interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

function toText(node: HastNode | undefined): string {
  if (!node) return '';
  if (node.type === 'text') return node.value ?? '';
  return (node.children ?? []).map(toText).join('');
}

function languageOf(node: HastNode | undefined): string {
  const className = node?.properties?.className;
  const classes = Array.isArray(className) ? className.map(String) : typeof className === 'string' ? [className] : [];
  const match = classes.find((c) => c.startsWith('language-'));
  return match ? match.slice('language-'.length) : '';
}

interface Props {
  node?: unknown;
  children?: ReactNode;
}

export default function CodeBlock({ node, children }: Props) {
  const pre = node as HastNode | undefined;
  const code = pre?.children?.find((c) => c.type === 'element' && c.tagName === 'code');
  const language = languageOf(code);
  const text = toText(code).replace(/\n$/, '');

  return (
    <div className="codeblock">
      <div className="codeblock__header">
        <span className="codeblock__lang">{language || 'text'}</span>
        <CopyButton text={text} label="Copy code" className="codeblock__copy" />
      </div>
      <pre className="codeblock__pre">{children}</pre>
    </div>
  );
}
