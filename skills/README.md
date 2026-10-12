# Ollambo Skills

This folder is reserved for future skill files.

A **skill** is a markdown file that teaches Ollambo a specialized workflow
(for example: "write a FastAPI backend", "review a pull request", etc.).

## How skills will work (planned)

1. Drop a `.md` file into this folder.
2. The file starts with YAML front-matter: `name`, `description`, `trigger`.
3. When the user's message matches the trigger, Ollambo loads the skill
   instructions into the system prompt automatically.

## Example (not implemented yet)

```markdown
---
name: fastapi-backend
description: Scaffold a FastAPI project with best practices
trigger: "build a FastAPI" OR "create an API"
---

When the user asks for a FastAPI project, follow these steps:
1. ...
```

Skills are loaded at runtime — no code changes needed to add one.
