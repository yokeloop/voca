---
tags:
  - voice-agent
  - v3
  - adr
status: agreed-pending-runtime-review
---

# ADR-0003: Code and test foundation

## Status

The VOCA-13 decision is agreed; its executable result is pending review. It does not state that this PR is accepted or merged.

## Context

VOCA-2 defined future voice contracts but deliberately shipped no runtime. The next work needs a small executable boundary that proves source-package installation, safe local diagnostics, durable session identity and snapshot recovery without implementing synthetic voice, tasks or providers.

## Decision

Build one Python/FastAPI application with explicit composition, a narrow core session service, a single locked SQLite connection, strict HTTP/control WebSocket wire models and a small React diagnostic panel. Use real SQLite/TestClient workflows for persistence and recovery, focused diagnostics tests, and a manual browser walkthrough for the browser entry.

The service/store boundary exists because current use cases need durable sessions and an adapter, not as a generic repository or Unit-of-Work framework. Lifespan owns store and logging resources. The browser owns only its socket and last applied cursor. Snapshot recovery has empty histories because no turns/messages/tasks exist yet.

## Consequences

Rejected approaches are: synthetic turns or scripted LLM behavior; framework/DI/plugin scaffolding; ORM, async storage, generic repository layers and future empty trees; duplicated tests across unit/API/browser; a browser-test framework before a browser risk justifies it. The cost is that VOCA-4 must add durable command dedup before it introduces side effects, and voice/hardware acceptance remains future work.

## References

- [VOCA-13](https://github.com/yokeloop/voca/issues/13)
- [Code architecture](../code-architecture.md)
- [Testing](../testing.md)
- [Diagnostics](../diagnostics.md)
- [ADR-0002](0002-conversation-lifecycle-and-roadmap.md)
