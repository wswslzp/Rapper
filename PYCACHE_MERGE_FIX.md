# __pycache__ Merge Conflict Fix

## Problem Description (Pitfall #5b)

When multiple Rapper instances work on the same repository in parallel:

1. **Scenario**: Two worktree branches modify the same Python files
2. **Issue**: `__pycache__` bytecode files get modified in both main branch and worktree
3. **Error**: Merge fails with:
   ```
   error: Your local changes to the following files would be overwritten by merge:
     lib/__pycache__/task_runner.cpython-311.pyc
   ```
4. **Additional Issue**: Uncommitted tracked files in main branch can also block merges

## Root Cause

- Python bytecode files (`__pycache__/*.pyc`) were being tracked by git
- No `.gitignore` to exclude these build artifacts  
- Multiple Rapper instances generate different bytecode, creating conflicts
- Standard `git merge` cannot handle binary file conflicts automatically

## Solution Implemented

### 1. Prevention (`.gitignore`)

Created comprehensive `.gitignore` to prevent future tracking:

```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so

# Virtual environments  
.env
.venv
# ... (full file in repo)
```

### 2. Enhanced Merge Logic (`do_merge()` in `rapper`)

Enhanced the `rapper --merge` command with intelligent conflict resolution:

**Pre-merge Conflict Detection:**
```bash
# 1. Check for uncommitted changes in main branch
main_dirty=$(git -C "$merge_target" status --porcelain)

# 2. Separate __pycache__ files from other changes
pycache_files=$(echo "$main_dirty" | grep '__pycache__')
other_files=$(echo "$main_dirty" | grep -v '__pycache__')
```

**Conflict Resolution:**
```bash
# 3. Discard __pycache__ conflicts (short-term fix)
for file in $pycache_files; do
    git checkout -- "$file" 2>/dev/null || rm -f "$file"
done

# 4. Stash other tracked files to avoid conflicts
if [[ -n "$other_files" ]]; then
    git stash push -m "rapper-merge-$(date +%s): pre-merge stash"
    stash_created=true
fi

# 5. Verify main branch is clean
git status --porcelain  # Should be empty

# 6. Perform merge safely
git merge "$branch_name"

# 7. Restore stashed changes if merge succeeded
if [[ $merge_success == true ]] && [[ $stash_created == true ]]; then
    git stash pop  # Handle conflicts gracefully
fi
```

**Error Recovery:**
- If merge fails, automatically restore original state
- Provide clear error messages with next steps
- Never leave repository in inconsistent state

### 3. Bulk Operations (`--merge-all`)

Added `rapper --merge-all` command for efficient bulk merging:

```bash
rapper --merge-all  # Interactive confirmation
# Processes all unmerged worktrees sequentially
# Applies enhanced conflict resolution to each
# Provides summary of successes/failures
```

### 4. Enhanced Diagnostics

Improved merge output with detailed conflict analysis:

```bash
⚠️ Main branch has uncommitted changes that may conflict:
  M  lib/__pycache__/daemon.cpython-311.pyc  
  M  lib/task_runner.py

🗑️ Discarding __pycache__ bytecode conflicts...
  Discarding: lib/__pycache__/daemon.cpython-311.pyc

💾 Stashing other uncommitted changes...
✓ Created stash: rapper-merge-1778765432: pre-merge stash

✅ Main branch is now clean for merge

🔄 Executing merge: git merge rapper/feature-branch
✓ Merged rapper/feature-branch successfully  

🔄 Merge successful - attempting to restore stashed changes...
✓ Successfully restored pre-merge stashed changes
```

## Usage

### Single Merge (Enhanced)
```bash
# Old behavior: Would fail with __pycache__ conflicts
# New behavior: Automatically resolves conflicts
rapper --merge task_12345
```

### Bulk Merge (New)
```bash
# Merge all unmerged worktrees safely
rapper --merge-all
```

### Manual Conflict Resolution (If Needed)
```bash
# Identify conflicts
git status --porcelain | grep __pycache__

# Discard bytecode conflicts  
git checkout -- lib/__pycache__/*.pyc

# Handle other changes
git stash push -m "pre-merge cleanup"

# Perform merge
git merge target-branch

# Restore changes
git stash pop
```

## Testing

### Validation Test
`test_enhanced_merge.py` demonstrates:
- ✅ Conflict detection works correctly
- ✅ __pycache__ files are properly discarded
- ✅ Other changes are safely stashed/restored
- ✅ Repository state is preserved on failure

### Reproduction Test  
`tests/test_pycache_merge_conflicts.py` confirms:
- ✅ Original problem can be reproduced (RED test)
- ✅ Enhanced logic resolves the conflicts (GREEN test)

## Benefits

1. **Parallel Safety**: Multiple Rapper instances can work without merge conflicts
2. **Automatic Resolution**: No manual intervention needed for common conflicts  
3. **State Preservation**: Uncommitted work is safely handled
4. **Clear Diagnostics**: Detailed output explains what happened
5. **Backward Compatibility**: Existing workflows continue to work
6. **Bulk Operations**: Efficient handling of multiple unmerged branches

## Future Improvements

1. **Git Attributes**: Configure `.gitattributes` for better binary handling
2. **Merge Strategies**: Explore `merge.ours` for systematic __pycache__ handling  
3. **Pre-commit Hooks**: Prevent __pycache__ tracking at commit time
4. **CI Integration**: Automated testing of parallel merge scenarios

---

## Resolution Status

✅ **RESOLVED**: Pitfall #5b is now fixed  
✅ **Prevention**: `.gitignore` prevents future tracking  
✅ **Resolution**: Enhanced merge logic handles existing conflicts  
✅ **Validation**: Comprehensive testing confirms the fix works

Multiple Rapper instances can now safely merge worktree branches without __pycache__ conflicts! 🎉