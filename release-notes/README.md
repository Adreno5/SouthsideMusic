# release-notes

每个版本的发布说明放这里, 文件名就是 tag 名:

    release-notes/v51.md

格式: **第一行是 Release 标题, 剩下的全部是正文**。

    # v51 Released - 窗口图标修复与自动发布

    # Features
    - 修复主窗口与启动窗口图标不显示的问题
    - 新增自动发布流程, 推 tag 即发版

    **Full Changelog**: https://github.com/Adreno5/SouthsideMusic/compare/v50...v51

用法: 和版本号一起提交, 然后推 tag。

    1. .\updates.bat                  (改成 v51)
    2. 写 release-notes/v51.md
    3. git add pyproject.toml installer.iss uv.lock release-notes/v51.md
       git commit -m "updated to v51"
       git push
    4. git tag v51 && git push origin v51

两点注意:

- 第一行要被当作标题, 必须**同时**满足: ① 以 `# ` 开头 ② 内容里含版本号(如 `v51`)。
  只满足 ① 是不够的 —— 正文里常见的 `# Features` 就不会被误当标题。
  不满足时整篇作为正文, 标题退化成 "v51 Released"。
- 文件不存在时, 工作流会退回 `--generate-notes` (自动生成提交列表), 不会失败。
