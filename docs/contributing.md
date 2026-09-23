# 贡献指南

仓库脚本与文档采用 MIT，上游包保留各自许可证。Python 使用 uv；不要添加独立 pip/venv 工作流。

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked python -m apk_repository validate
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m apk_repository plan --include-disabled
```

Python 静态检查使用 Ruff，规则包含默认检查和导入排序。完整下载、签名和安装检查在 GitHub Actions 中执行。PR 不使用生产密钥。

新增包在 `packages/<name>/package.json` 中声明固定上游、APK 资产名称、版本、架构、许可证和依赖，再注册到 `repository.json`。只接收适用的上游 APK v3；不要把 Android 或 Alpine APK 当成 OpenWrt APK。配置变化应附来源与审核说明。

新增架构须同步补充 CI 安装检查。发布只保留最新正式版和最新预发布版，不引入历史产物合并或版本缓存到站点。详见[配置说明](configuration.md)。

不要提交签名私钥、访问令牌、下载缓存、生成包或发布目录。
