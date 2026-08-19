# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities privately to **roksstartups@gmail.com** rather than
opening a public issue. Include enough detail to reproduce the problem (affected file/module,
steps, impact). We'll acknowledge within a few days and follow up once it's triaged.

## Scope

This is a self-hosted tool: once you deploy it, securing your own environment (API keys,
network exposure, reverse proxy/auth in front of the UI, container/host hardening) is your
responsibility — see the README's [Security notes for
self-hosters](README.md#security-notes-for-self-hosters). Vulnerabilities in the project's own
code (e.g. an injection point, an auth bypass in a future multi-tenant mode, a way to make the
worker fetch non-YouTube URLs) are in scope and appreciated.
