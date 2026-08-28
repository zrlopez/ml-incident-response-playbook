import fs from 'node:fs';
import path from 'node:path';
import matter from 'gray-matter';

export type DocPage = {
  slug: string[];
  route: string;
  sourcePath: string;
  title: string;
  content: string;
};

export type NavItem = {
  title: string;
  href?: string;
  children?: NavItem[];
};

const DOCS_ROOT = path.resolve(process.cwd(), '..', 'docs');
const REPO_ROOT = path.resolve(process.cwd(), '..');
const GITHUB_BASE = 'https://github.com/zrlopez/ml-incident-response-playbook/blob/main';

function titleFromName(name: string): string {
  return name
    .replace(/\.mdx?$/, '')
    .replace(/^index$/, 'Home')
    .replace(/[_-]/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bMl\b/g, 'ML')
    .replace(/\bApi\b/g, 'API')
    .replace(/\bCi\b/g, 'CI')
    .replace(/\bCd\b/g, 'CD')
    .replace(/\bJwt\b/g, 'JWT')
    .replace(/\bOrm\b/g, 'ORM');
}

function readMeta(dir: string): Record<string, string | { title?: string; display?: string }> {
  const file = path.join(dir, '_meta.json');
  if (!fs.existsSync(file)) return {};
  return JSON.parse(fs.readFileSync(file, 'utf8')) as Record<string, string | { title?: string; display?: string }>;
}

function metaTitle(meta: Record<string, string | { title?: string; display?: string }>, key: string, fallback: string): string {
  const value = meta[key];
  if (typeof value === 'string') return value;
  if (value?.title) return value.title;
  return fallback;
}

function isHidden(meta: Record<string, string | { title?: string; display?: string }>, key: string): boolean {
  const value = meta[key];
  return typeof value === 'object' && value.display === 'hidden';
}

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name.startsWith('.') || entry.name === '_meta.json') return [];
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    if (/\.mdx?$/.test(entry.name)) return [full];
    return [];
  });
}

function slugFromFile(file: string): string[] {
  const rel = path.relative(DOCS_ROOT, file).replace(/\\/g, '/').replace(/\.mdx?$/, '');
  if (rel === 'index') return [];
  return rel.split('/').filter((part) => part !== 'index');
}

function routeFromSlug(slug: string[]): string {
  return `/${slug.join('/')}`.replace(/\/$/, '') || '/';
}

function extractTitle(content: string, file: string): string {
  const match = content.match(/^#\s+(.+)$/m);
  return match?.[1]?.replace(/`/g, '') ?? titleFromName(path.basename(file));
}

export function getAllPages(): DocPage[] {
  return walk(DOCS_ROOT).map((file) => {
    const parsed = matter(fs.readFileSync(file, 'utf8'));
    const slug = slugFromFile(file);
    return {
      slug,
      route: routeFromSlug(slug),
      sourcePath: file,
      title: extractTitle(parsed.content, file),
      content: parsed.content,
    };
  });
}

export function getPage(slug: string[]): DocPage | undefined {
  return getAllPages().find((page) => page.route === routeFromSlug(slug));
}

export function getNav(dir = DOCS_ROOT, baseSlug: string[] = []): NavItem[] {
  const meta = readMeta(dir);
  const entries = fs.readdirSync(dir, { withFileTypes: true }).filter((entry) => !entry.name.startsWith('.') && entry.name !== '_meta.json');
  const byKey = new Map(entries.map((entry) => [entry.name.replace(/\.mdx?$/, ''), entry]));
  const orderedKeys = [...Object.keys(meta), ...entries.map((entry) => entry.name.replace(/\.mdx?$/, ''))]
    .filter((key, index, arr) => arr.indexOf(key) === index)
    .filter((key) => byKey.has(key) && !isHidden(meta, key));

  return orderedKeys.flatMap((key): NavItem[] => {
    const entry = byKey.get(key);
    if (!entry) return [];
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      const children = getNav(full, [...baseSlug, entry.name]);
      if (children.length === 0) return [];
      return [{ title: metaTitle(meta, key, titleFromName(entry.name)), children }];
    }
    if (!/\.mdx?$/.test(entry.name)) return [];
    const slug = slugFromFile(full);
    return [{ title: metaTitle(meta, key, titleFromName(entry.name)), href: routeFromSlug(slug) }];
  });
}

export function resolveDocHref(href: string | undefined, currentSourcePath: string): string | undefined {
  if (!href || href.startsWith('#') || /^[a-z]+:/i.test(href)) return href;
  const [rawPath, hash] = href.split('#');
  const target = path.resolve(path.dirname(currentSourcePath), rawPath);
  if (target.startsWith(DOCS_ROOT)) {
    const withoutExt = target.replace(/\.mdx?$/, '');
    const rel = path.relative(DOCS_ROOT, withoutExt).replace(/\\/g, '/');
    const route = rel === 'index' ? '/' : `/${rel.replace(/\/index$/, '')}`;
    return hash ? `${route}#${hash}` : route;
  }
  if (target.startsWith(REPO_ROOT)) {
    const rel = path.relative(REPO_ROOT, target).replace(/\\/g, '/');
    return `${GITHUB_BASE}/${rel}`;
  }
  return href;
}
