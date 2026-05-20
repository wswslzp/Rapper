# Reviewer Claim Live E2E Restart Probe

- **Task ID**: 20260520-224414-bshh
- **Current Time**: Wed May 20 10:44:40 PM JST 2026
- **Status**: reviewer claim column preservation live e2e probe

## Purpose
This is a post-fix live E2E verification probe for bug `task_af5bec5e9ffdcc39`. 
This probe validates that after a restart, live daemon/reviewer correctly loads new code 
and preserves the correct column state during reviewer claim operations.

## Expected Behavior
1. Task should move from `doing` to `review` after Rapper completion
2. When reviewer claims the task, it must remain in `column=review` 
3. Only `assignee=reviewer-*` or metadata/reviewState should indicate review status
4. NEVER allow `column=doing && assignee=reviewer-*`
5. Reviewer PASS should move task to `done`