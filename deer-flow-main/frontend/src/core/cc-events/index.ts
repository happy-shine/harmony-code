// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: Apache-2.0

export type {
  StreamEvent,
  CCBlock,
  CCAssistantEvent,
  CCUserEvent,
  CCSystemInitEvent,
  CCResultEvent,
  CCAdapterEvent,
} from "./types";

export { parseSSEFrame, drainSSE } from "./parse";
export type { ParsedFrame } from "./parse";
