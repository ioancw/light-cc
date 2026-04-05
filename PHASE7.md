# Phase 7: DB-Backed Memory + Optimistic UI

Deferred from the phases 0-6 refactoring (2026-04-03). These two items are independent of each other and of most other phases.

## Item 9: DB-Backed Memory

**Goal:** Move user memory from filesystem (`data/users/<user-id>/memory/*.md`) to a database table, enabling multi-instance deployment behind a load balancer.

**New files:**
- `alembic/versions/xxx_add_memories.py` -- Migration for `memories` table

**Files to modify:**

- `core/db_models.py` -- Add `Memory` ORM model:
  ```python
  class Memory(Base):
      id: str           # UUID primary key
      user_id: str      # FK to users
      title: str        # Memory title / name
      content: str      # Markdown content body
      type: str         # "user", "feedback", "project", "reference"
      tags: str         # Comma-separated tags (optional)
      created_at: datetime
      updated_at: datetime
  ```

- `memory/manager.py` -- Replace file-based operations with DB-backed equivalents:
  - `save_memory()` -> INSERT or UPDATE in memories table
  - `load_memory()` -> SELECT all memories for user, format as markdown
  - `read_memory()` -> SELECT single memory by title/id
  - `search_memory()` -> LIKE/ILIKE query on title + content
  - `list_memories()` -> SELECT all for user
  - `delete_memory()` -> DELETE by title/id
  - Keep file-based implementation as a fallback/migration path
  - Add `migrate_file_memories_to_db(user_id)` for one-time migration of existing file-based memories

**Migration strategy:**
1. Add the DB table via alembic migration
2. On first load for a user, check if file-based memories exist but DB is empty -- auto-migrate
3. Keep the file reader as a fallback for the migration period
4. After all users have been migrated, remove file-based code

---

## Item 11: Optimistic UI

**Goal:** Add loading states and skeleton UI for conversation operations so the app feels responsive on slow connections or large conversation loads.

**Files to modify:**

- `frontend/src/state.svelte.js`
  - Add `loadingConversations: new Set()` to `appState`
  - Add helpers: `setConversationLoading(cid)`, `clearConversationLoading(cid)`, `isConversationLoading(cid)`

- `frontend/src/ws.js`
  - When sending `resume_conversation`, add cid to `loadingConversations` set
  - In `conversation_loaded` handler, remove cid from `loadingConversations` set
  - On error for a cid, also remove from loading set

- `frontend/src/components/ChatArea.svelte`
  - Show a skeleton/spinner when the current conversation is in the loading set
  - The skeleton should show 3-4 placeholder message rows with animated shimmer
  - When `conv.messages` is empty and loading is true, show skeleton instead of empty state

- `frontend/src/components/Sidebar.svelte`
  - Show a subtle spinner icon on conversation items that are currently loading
  - Could be a small rotating indicator next to the conversation title

**Skeleton design:**
- Use CSS `@keyframes shimmer` with a gradient moving left-to-right
- 3 placeholder rows alternating "user" (short, right-aligned) and "assistant" (longer, left-aligned)
- Match the existing dark theme colors (use `var(--bg-tertiary)` or similar)
- Fade out when real content arrives
