# docnums 演示（公开仓库里也能跑）

这个文件用来给 CI 一个**真实可校验**的样例：它引用的两个数都来自
`tools/audit_numbers.py`，而那两项统计只覆盖 `src/` 与 `tests/`——
两者都在公开仓库里，所以这个检查在**没有内部文档**的镜像仓里同样成立。

- 测试文件：86<!--dn:test_files--> 个（`tests/**.py` 里 `test_*.py`）
- 断言调用点：1,402<!--dn:check_calls--> 处（`tests/**.py` 里 `check(` 出现次数）

```bash
# 校验（不一致就非零退出）
docnums --check cpp/docnums/examples/demo.md
```

> 提醒：这两个数会随**新增测试**而变——那时 `--check` 会报错，
> 这正是它存在的意义（改完测试记得回填这两个数）。
