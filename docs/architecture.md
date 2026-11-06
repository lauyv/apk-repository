# 发布架构

```text
检查上游更新 → assemble 下载并校验 → sign 签名、索引与全站验证 → deploy
                                                   ↓ 手动按需
                                           两架构安装检查通过后部署
```

每日北京时间 04:00 检查上游 Release、提交、资产 ID 和摘要。只查询发布列表首页，取首个非草稿版本，不扫描历史页；签名前按选定标签精确复核。无变化则跳过构建；站点 404 触发首次发布，其他错误使检查失败。手动触发始终重新发布，适用于配置、代码和密钥变更。

`assemble` 不持有生产私钥，下载上游现成 OpenWrt APK 并校验 SHA-256、大小、包名、版本和架构。只生成无签名候选和清单，不生成索引。

`sign` 使用 `repository-signing` 环境中的密钥，独立查询上游并核对候选来源。追加 APK 签名后比较元数据，生成并签名 `packages.adb`，写入静态下载列表和安装说明，最后生成 `SHA256SUMS` 及独立签名。完整站点验证在该 job 中执行一次，通过后上传 Pages artifact。

手动启用安装检查时，另行上传候选供两个无私钥的 job 使用。检查直接导入固定 OpenWrt 25.12.0 rootfs，通过 HTTP 访问源，解析依赖、安装和移除包，并运行 `sing-box version`。使用 `--no-scripts`，不覆盖安装钩子、真实设备的 procd、TUN 和网络功能。安装检查不编译仓库工具链。

```text
public/
  index.html
  install.txt
  manifest.json
  SHA256SUMS
  SHA256SUMS.sig
  apk/
    apk-repository.pem
    latest/
      x86_64/
        packages.adb
        manifest.json
        <package>-<version>.apk
      aarch64_generic/
        packages.adb
        manifest.json
        <package>-<version>.apk
```

站点从空目录生成，整体部署，只含当前包和索引。页面为按架构分组的静态下载列表。设备直接安装公钥和配置 latest 源；不生成 bootstrap 配置包。

APK Tools 二进制使用精确配置和构建脚本作为缓存键，命中后检查版本。上游下载缓存按 SHA-256 复用，OpenWrt rootfs 按固定摘要缓存；使用前均重新校验。未缓存签名站点，不传输旧站点来复用签名。Actions 中间 artifact 保留 1 天。

`deploy` 只接受成功签名和验证的 Pages artifact；启用安装检查时还须等待两架构检查成功。发布失败保留现有线上站点。
