# 实现状态

发布实现由 GitHub Actions 执行构建和验证。是否已成功上线，以仓库的最新 Actions 部署结果为准。

| 项目 | 实现 |
|---|---|
| Git 仓库、MIT、贡献与安全说明 | 已添加 |
| uv 项目与锁文件 | 已添加 |
| Ruff 静态检查 | 开发依赖与 CI 已接入 |
| 通用包配置、校验和预览 | `apk_repository/config.py` |
| Release 选择、版本映射、资产摘要校验 | `apk_repository/upstream.py` |
| 直接同步上游 OpenWrt APK | `apk_repository/build.py` |
| 签名、索引、bootstrap 和站点 | `apk_repository/publish.py` |
| APK Tools 固定提交构建 | `scripts/prepare-tools.sh` |
| CI 测试与发布工作流 | `.github/workflows/` |
| OpenWrt 容器安装检查 | `scripts/check-openwrt.sh` |
| 手动、每日定时发布 | `publish.yml` |

运行前提为 GitHub Pages 的 Actions 发布源、受保护的签名环境和 `APK_SIGNING_KEY`。工作流的权限与阶段边界见[发布架构](architecture.md)，该文档只记录实现范围，不包含维护者的个人部署清单。

只有首次远端工作流通过，才能确认这些脚本在实际 CI 环境中的执行结果。当前代码不将配置中的启用状态或接入审核标记作为已通过运行验证的证明。

后续扩展可增加更多上游 APK 和经过检查的 OpenWrt 架构；不需要把仓库绑定到 sing-box。
