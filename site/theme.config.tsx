import React from "react";
import type { DocsThemeConfig } from "nextra-theme-docs";
import { useConfig } from "nextra-theme-docs";

const SITE_NAME = "ML Incident Response Playbook";

const config: DocsThemeConfig = {
  logo: <span>{SITE_NAME}</span>,
  project: {
    link: "https://github.com/zrlopez/ml-incident-response-playbook",
  },
  docsRepositoryBase:
    "https://github.com/zrlopez/ml-incident-response-playbook/tree/main/site",
  head: function Head() {
    const { frontMatter, title: pageTitle } = useConfig();
    const title =
      !pageTitle || pageTitle === SITE_NAME
        ? SITE_NAME
        : `${pageTitle} | ${SITE_NAME}`;
    const description =
      (typeof frontMatter?.description === "string" &&
        frontMatter.description) ||
      "Production-grade FastAPI service and runbooks for ML incident detection, triage, and resolution.";
    const canonical =
      typeof frontMatter?.canonical === "string"
        ? frontMatter.canonical
        : undefined;
    const image =
      typeof frontMatter?.image === "string" ? frontMatter.image : undefined;

    return (
      <>
        <title>{title}</title>
        <meta property="og:title" content={title} />
        <meta name="description" content={description} />
        <meta property="og:description" content={description} />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content={title} />
        <meta name="twitter:description" content={description} />
        {canonical ? <link rel="canonical" href={canonical} /> : null}
        {image ? <meta property="og:image" content={image} /> : null}
      </>
    );
  },
  footer: {
    content: `© ${new Date().getFullYear()} Zachary Ryan Lopez`,
  },
};

export default config;
