import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';
import { getNav } from '../lib/docs';

export const metadata: Metadata = {
  title: {
    default: 'ML Incident Response Playbook',
    template: '%s · ML Incident Response Playbook',
  },
  description: 'Production-style ML incident response docs, runbooks, architecture, and MLOps portfolio evidence.',
};

function NavList({ items }: { items: ReturnType<typeof getNav> }) {
  return (
    <ul>
      {items.map((item) => (
        <li key={`${item.href ?? item.title}`}>
          {item.href ? <Link href={item.href}>{item.title}</Link> : <span>{item.title}</span>}
          {item.children ? <NavList items={item.children} /> : null}
        </li>
      ))}
    </ul>
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const nav = getNav();
  return (
    <html lang="en">
      <body>
        <div className="shell">
          <aside className="sidebar">
            <Link className="brand" href="/">
              <span className="brand-mark">🚨</span>
              <span>ML Incident</span>
            </Link>
            <nav aria-label="Documentation navigation">
              <NavList items={nav} />
            </nav>
          </aside>
          <main className="content">{children}</main>
        </div>
      </body>
    </html>
  );
}
