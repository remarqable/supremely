# Changelog

All notable changes to Supremely are documented in this file.
Format follows [Common Changelog](https://common-changelog.org).

## [0.1.0] - 2026-09-02

### Added

- **Self-hosted publishing and community platform** — Supremely as an open-source Flask application one organization or a whole host of them can run. Installing, publishing, and onboarding never require an email service
- **Multi-tenant by design** — the organization is the tenant, resolved per request from a subdomain or a custom domain, with tenant isolation enforced in one place rather than in every query
- **Content types** — everything an organization publishes is content with a type: articles, pages, events, announcements, podcast episodes, recordings, resources, and a Team roster. Adding a vertical means registering one type, not touching the publishing system
- **The community application** — a shared surface for members and admins: a home feed, discussions with topics and threaded replies, reactions, a member directory, and notifications. Management controls appear inline where the object lives, marked so an admin can see what a member cannot
- **Discussions** — groups, pinned and locked posts, moderation, and an organization-wide visibility switch (per-group, fully public, or members-only)
- **Newsletters** — subscriber list with double opt-in, sending published content as an issue, a public archive, and one-click unsubscribe
- **Memberships and gated content** — public or members-only per item, per section, and per discussion group. Gated content is teased rather than hidden by default: visitors see locked titles and a members-only page explaining what membership unlocks, and an organization can turn teasing off
- **Themes** — a theme is a manifest and some HTML, with a documented contract in both directions. Templates resolve through a WordPress-style hierarchy falling back to the built-in Origin theme, so a five-file theme restyles an entire site. A theme asks for content by type (`latest_content`, `content_count`) and renders it without a line of application code written for it; it never decides who may see anything
- **Separate site and community names** — an organization whose public site is named differently from its community (a community called "Acme Community" whose website is just "Acme") sets a Site name under Manage → Branding. It belongs to the organization, so it survives a change of theme; the Supremely theme's own "Site name" field, which could not, is gone and any value stored in it is carried across on upgrade
- **Theme editor** — themes declare editable copy and image fields in their manifest and Supremely generates the editing form, so an organization supplies its own words and pictures without touching code. Its name, description, logo, favicon and hero image belong to the organization and survive a theme change
- **Four built-in themes** — Origin (the default and universal fallback), Supremely (the project's marketing theme), Midnight (a dark variant), and Trailhead (the worked example from the theme tutorial)
- **Plugins** — versioned, per-organization installable features that can register their own content types and routes, enabled and disabled without a restart
- **Media library** — uploads with magic-byte type sniffing, EXIF stripping, size and pixel ceilings, generated thumbnails, alternative text, and visibility-checked serving
- **Navigation** — configurable primary and footer menus, including dropdowns and footer link columns
- **Management console** — `/manage` for an organization (content, categories, media, members, discussions, newsletter, theme, branding, navigation, plugins, privacy, analytics, domains) and `/admin` for the operator (organizations, users, themes, installation settings)
- **Setup wizard** — a fresh installation serves only the wizard until it is configured; no configuration files to hand-edit before first boot
- **Background jobs** — a database-backed queue and worker process for email batches and other out-of-request work, with no extra infrastructure to run
- **Analytics** — an organization can point its own Plausible, Umami, or Google Analytics account at its site; the management console is never tracked
- **Custom domains** — an organization can serve its site from its own domain
- **Internationalization** — every interface string goes through a translation catalog, and layouts use logical properties so right-to-left languages work
- **SQLite by default, PostgreSQL when you need it** — one environment variable, no rewrite. The test suite runs on both
- **Docker deployment** — a published image, a two-service compose file, and an install script for a fresh Ubuntu host
