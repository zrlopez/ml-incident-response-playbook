import React from "react";
import type { DocsThemeConfig } from "nextra-theme-docs";

const config: DocsThemeConfig = {
  logo: <span>ML Incident Response Playbook</span>,
  project: {
    link: "https://github.com/zrlopez/ml-incident-response-playbook",
  },
  docsRepositoryBase:
    "https://github.com/zrlopez/ml-incident-response-playbook/edit/main/docs",
  footer: {
    content: `© ${new Date().getFullYear()} Zachary Ryan Lopez`,
  },
};

export default config;
