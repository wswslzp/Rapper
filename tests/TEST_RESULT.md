# Worktree 隔离功能验证结果

## 概要

测试了 `_make_worktree_safe_prompt()` 函数的正确性，发现并修复了一个正则表达式bug，确保worktree隔离功能完全正常工作。

## 测试结果

```bash
$ cd /app/rapper && python3 -m pytest tests/test_worktree_isolation.py -v
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.0.3, pluggy-1.6.0 -- /app/rapper/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /app/rapper
configfile: pyproject.toml
plugins: anyio-4.13.0
collecting ... collected 8 items

tests/test_worktree_isolation.py::test_replaces_absolute_path_with_relative PASSED [ 12%]
tests/test_worktree_isolation.py::test_replaces_path_with_trailing_slash PASSED [ 25%]
tests/test_worktree_isolation.py::test_replaces_exact_repo_root PASSED   [ 37%]
tests/test_worktree_isolation.py::test_does_not_replace_unrelated_paths PASSED [ 50%]
tests/test_worktree_isolation.py::test_guard_prepended PASSED            [ 62%]
tests/test_worktree_isolation.py::test_guard_contains_worktree_path PASSED [ 75%]
tests/test_worktree_isolation.py::test_complex_replacement_scenario PASSED [ 87%]
tests/test_worktree_isolation.py::test_repo_workdir_with_trailing_slash PASSED [100%]

============================== 8 passed in 0.02s ===============================
```

✅ **8/8 测试通过**

## 发现的Bug及修复

### Bug描述
原始的正则表达式只匹配路径后面有斜杠(`/`)或在字符串结尾(`$`)的情况：
```python
re.escape(repo_root) + r"(/|$)"
```

这导致 `"Update /app/myrepo and commit"` 中的 `/app/myrepo` 没有被替换，因为后面跟着空格而不是斜杠或字符串结尾。

### 修复方案
更新正则表达式逻辑为两步替换：
```python
# First: replace repo_root followed by "/" 
safe_prompt = re.sub(re.escape(repo_root) + r"(/)", r"./", prompt)
# Then: replace standalone repo_root (followed by space/end)
safe_prompt = re.sub(re.escape(repo_root) + r"(?=\s|$)", ".", safe_prompt)
```

### 修复验证
修复前：
```
4. Update /app/myrepo and commit  # ❌ 没有被替换
```

修复后：
```
4. Update . and commit  # ✅ 正确替换
```

## 测试覆盖的场景

1. **基本路径替换**: `/app/myrepo/src/foo.py` → `./src/foo.py`
2. **带斜杠结尾**: `/app/myrepo/` → `./`
3. **精确repo根路径**: `/app/myrepo` → `.`
4. **不相关路径保持不变**: `/app/other-project/file.py` 保持不变
5. **Guard正确添加**: 包含隔离警告和worktree路径信息
6. **复杂场景**: 多种路径类型混合的场景
7. **边界情况**: repo_workdir带结尾斜杠的处理

## 函数正确性确认

`_make_worktree_safe_prompt()` 函数现在能够正确：

1. ✅ 将主repo的绝对路径替换为相对路径
2. ✅ 在prompt前添加隔离约束指令
3. ✅ 处理各种边界情况（带/不带结尾斜杠、空格分隔等）
4. ✅ 保持不相关路径不变
5. ✅ 提供清晰的worktree隔离指导信息

## 文件清单

- `tests/test_worktree_isolation.py` - 完整的测试套件
- `tests/debug_worktree.py` - 调试脚本（可删除）
- `tests/debug_complex.py` - 复杂场景调试脚本（可删除）
- `lib/task_runner.py` - 修复了正则表达式bug

## 结论

Worktree隔离功能验证完成，发现的bug已修复，所有测试通过。该功能现在可以安全防止Claude Code在worktree模式下误操作主repo。