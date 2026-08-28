import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import type { AnchorHTMLAttributes, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { type DocPage, getAllPages, getPage, resolveDocHref } from '../../lib/docs';

export function generateStaticParams() {
  return getAllPages().map((page) => ({ slug: page.slug }));
}

export function generateMetadata({ params }: { params: { slug?: string[] } }): Metadata {
  const page = getPage(params.slug ?? []);
  return {
    title: page?.title ?? 'Not Found',
  };
}

export default function DocPage({ params }: { params: { slug?: string[] } }) {
  const found = getPage(params.slug ?? []);
  if (!found) return notFound();
  const page: DocPage = found;

  return (
    <article className="doc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) {
            const resolved = resolveDocHref(href, page.sourcePath);
            const external = resolved?.startsWith('http');
            if (!resolved) return <>{children}</>;
            return external ? (
              <a href={resolved} target="_blank" rel="noreferrer">
                {children}
              </a>
            ) : (
              <Link href={resolved}>{children}</Link>
            );
          },
        }}
      >
        {page.content}
      </ReactMarkdown>
    </article>
  );
}
