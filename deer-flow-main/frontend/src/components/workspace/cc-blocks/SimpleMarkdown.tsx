"use client";

import { memo } from "react";
import { Streamdown } from "streamdown";
import remarkGfm from "remark-gfm";

const remarkPlugins = [remarkGfm];

export const SimpleMarkdown = memo(function SimpleMarkdown({
  children,
}: {
  children: string;
}) {
  return <Streamdown remarkPlugins={remarkPlugins}>{children}</Streamdown>;
});
