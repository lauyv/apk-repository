# 发布架构

```text
GitHub Release API
  → 选择最新正式版 / 最新预发布版
  → 下载现成 OpenWrt APK，验证 GitHub SHA-256 与大小
  → 校验 APK v3 的包名、版本、架构、许可证、依赖
  → 固定 Release 标签对应的提交，保存源码归档和 LICENSE
  → 无签名候选 artifact
  → repository-signing 环境审批
  → 追加仓库签名、构建 bootstrap、签名 packages.adb
  → 全站 SHA256SUMS 与独立签名
  → 无生产密钥的 OpenWrt 容器安装检查
  → 整体部署 GitHub Pages
```

不执行上游 APK 的构建脚本，也不从二进制归档重新封装 sing-box。上游包的数据和元数据保留，仅签名块发生变化，manifest 分别记录原始资产摘要与发布 APK 摘要。

## 产物

```text
public/
  index.html
  install.txt
  manifest.json
  SHA256SUMS
  SHA256SUMS.sig
  apk/
    apk-repository.pem
    stable/x86_64/
      packages.adb
      manifest.json
      sing-box-<latest-stable-version>.apk
      luci-app-sing-box-<latest-stable-version>.apk
      apk-repository-stable-<version>.apk
    prerelease/x86_64/
      packages.adb
      manifest.json
      sing-box-<latest-prerelease-version>.apk
      luci-app-sing-box-<selected-version>.apk
      apk-repository-prerelease-<version>.apk
  sources/<package>/<commit>.tar.gz
  sources/<package>/<commit>.LICENSE
```

每次都从新目录生成两个通道，仅保留当前版本。没有历史版本目录、增量合并或线上回滚副本。同一上游提交在两个通道复用时，源码归档只保存一份。

索引显式使用 `${name}-${version}.apk` 的相对文件名；设备配置完整 `packages.adb` 地址。发布前检查索引集合与 manifest 一致，没有遗漏、多余包或历史版本。

## Bootstrap 与设备配置

各通道的 bootstrap APK 安装两个软件源配置文件，并附带自身 MIT 许可证：

- `/etc/apk/keys/apk-repository.pem`
- `/etc/apk/repositories.d/apk-repository.list`

两个 bootstrap 包互相冲突，防止无意中同时启用两个通道；只配置本仓库专用文件，不修改 `customfeeds.list` 或官方 `distfeeds.list`。第一次安装先核对并放置公钥，再安装已签名的 bootstrap。

源使用 `@apk_repository` tag。设备通过 `apk add -u sing-box@apk_repository luci-app-sing-box@apk_repository` 更新指定包。保留官方 feeds 解析依赖，不自动更新整个系统。

## CI 边界

`assemble` 没有签名密钥；`sign` 只处理和签名包，不安装或执行包脚本；后续 `verify` job 不接触生产私钥。固定 OpenWrt 25.12.0 x86_64 rootfs 的 URL 和 SHA-256 来自[官方镜像列表](https://downloads.openwrt.org/releases/25.12.0/targets/x86/64/)。

容器通过 HTTP 读取实际 feed，完成依赖解析、安装、指定包更新和移除，并运行 `sing-box version`。容器使用 `--no-scripts` 安装，避免加载宿主机内核模块；此检查不代替真实路由器上的 procd、TUN 和网络功能测试。
