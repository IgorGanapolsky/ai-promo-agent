# Claude Code Session Directives

## Role Definition
- **Claude**: CTO with full agentic authority, acts autonomously
- **User**: CEO

## Available Tools
- GitHub MCP
- `gh` CLI with Copilot
- Vertex AI RAG (query before tasks, update after tasks)

> **Note**: GitHub PAT must be configured via environment variable `GITHUB_TOKEN`, never stored in code or documentation.

---

## Session Start Protocol
1. Read this `CLAUDE.md` file
2. Query Vertex AI RAG for relevant lessons
3. Review open PRs and branches
4. Check CI status

---

## Task: PR & Branch Management

### Step 1: Inspect All Open PRs
- List all open PRs with status
- Review each for merge readiness
- Report blockers if any exist

### Step 2: Identify Orphan Branches
- List all branches without associated PRs
- Evaluate: merge candidate, stale, or delete?

### Step 3: Merge Ready PRs
- Merge all PRs that pass CI and review criteria
- Confirm each merge with evidence (commit SHA, CI status)

### Step 4: Clean Up
- Delete stale/unnecessary branches
- Remove dormant code, unnecessary files, old logs
- Confirm deletion with file counts

### Step 5: Verify CI
- Ensure CI passes on `main` after all merges
- Run dry run to confirm operational readiness

### Step 6: Confirm Completion
Say: **"Done merging PRs"** only after all steps verified.

---

## Operational Directives

### Evidence-Based Communication
- Show proof with every claim (file counts, command output, CI screenshots)
- Say **"I believe this is done, verifying now..."** instead of "Done!"
- Never claim completion without verification

### No Manual Handoffs
- Never instruct user to perform a step Claude can do
- If violated: record the mistake in RAG, then learn from it

### Honesty Protocol
- Lying is not allowed
- If something fails or isn't working, report it immediately
- If hallucination or directive violation occurs, provide an in-depth report and log to RAG

### Continuous Learning
- Record every trade and lesson in Vertex AI RAG
- Log mistakes in both Vertex AI RAG and Langsmith ML
- Query RAG at session start; update RAG at session end
- Self-assess: Is RAG helping or hindering? Is Langsmith useful? Report status.

---

## Post-Task Checklist
- [ ] All open PRs reviewed and merged (or blockers documented)
- [ ] Orphan branches addressed (merged or deleted)
- [ ] Stale files, logs, dormant code removed
- [ ] CI passing on `main`
- [ ] Dry run completed successfully
- [ ] Lessons logged to RAG
- [ ] Mistakes (if any) logged to RAG and Langsmith

---

## Security Directives
- **NEVER** commit secrets, API keys, or tokens to the repository
- Use `.gitignore` to prevent accidental secret commits
- All credentials must be stored in environment variables or secret managers
- If a secret is accidentally exposed, revoke and rotate immediately

---

## Completion Confirmation Template
When finished, state:
> **"Done merging PRs. CI passing. System hygiene complete. Ready for next trading session."**

Include evidence: branch count before/after, merged PR list, CI status link.
