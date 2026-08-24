# Engineering Principles

This document describes how Supremely thinks about engineering. It intentionally does **not** replace the project's implementation blueprint.

The blueprint is authoritative for concrete architecture, stack, coding conventions, and implementation constraints.

## 1. AI-first, human-accountable

AI is a primary implementation tool.

Humans remain responsible for:

- requirements;
- architecture;
- security;
- verification;
- testing;
- dependency choices;
- maintainability;
- what ships.

The provenance of code does not change its quality bar.

## 2. Blueprint before improvisation

AI performs best inside explicit constraints.

Implementation should conform to the project's blueprint rather than allowing individual prompts, agents, or contributors to invent competing conventions.

When reality proves the blueprint wrong, change the blueprint deliberately.

## 3. Prefer simplicity

Choose the smallest design that cleanly solves the current problem.

Avoid abstraction created only because it may someday become useful.

## 4. Keep the system understandable

A capable engineer should be able to trace important behavior without reconstructing the system from prompts or tribal knowledge.

Important decisions belong in code, documentation, tests, or ADRs.

## 5. Test behavior that matters

Tests exist to increase confidence, not to manufacture coverage numbers.

Prioritize critical behavior, security boundaries, data integrity, regressions, and interfaces.

## 6. Security is part of implementation

Secrets never belong in the repository.

Treat authentication, authorization, external input, dependency selection, data boundaries, and generated security-sensitive code with appropriate scrutiny.

## 7. The repository is the record

Important engineering knowledge should survive individual conversations with AI tools.

If future maintainers need to know it, capture it in the repository.

## 8. Change is expected

Pre-1.0 software will evolve.

Prefer designs that are easy to change over designs optimized for predictions about the distant future.
