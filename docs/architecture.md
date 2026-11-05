# 发布架构

```text
GitHub Release API
  → stable 选择正式版 / latest 选择最近发布版
  → 下载或复用缓存中的现成 OpenWrt APK，验证 GitHub SHA-256 与大小
  → 校验 APK v3 数据、包名、版本、架构
  → 固定 Release 标签对应的提交，保存源码归档
  → 无签名候选 artifact
  → repository-signing 环境审批
  → 复用兼容的已签名 APK 或追加仓库签名，重新构建 bootstrap、签名 packages.adb
  → 全站 SHA256SUMS 与独立签名
  → 完整性校验，以及手动按需启用的无生产密钥 OpenWrt 容器安装检查
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
    latest/x86_64/
      packages.adb
      manifest.json
      sing-box-<latest-published-version>.apk
      luci-app-sing-box-<latest-published-version>.apk
      apk-repository-latest-<version>.apk
  sources/<package>/<commit>.tar.gz
```

每次都从新目录生成两个通道，仅保留当前版本。未变更 APK 可复用上次经过验证的签名产物，但不合并旧站点目录；索引、bootstrap、发布清单和全站校验文件每次重新生成。没有历史版本目录或线上回滚副本。同一上游提交在两个通道复用时，源码归档只保存一份。

索引显式使用 `${name}-${version}.apk` 的相对文件名；设备配置完整 `packages.adb` 地址。发布前检查索引集合与 manifest 一致，没有遗漏、多余包或历史版本。

上图以 x86_64 为例；stable 和 latest 下均生成 `aarch64_generic/`。两架构均参与更新检查、发布完整性校验和容器安装检查。

## Bootstrap 与设备配置

各通道的 bootstrap APK 安装两个软件源配置文件，并附带自身 MIT 许可证：

- `/etc/apk/keys/apk-repository.pem`
- `/etc/apk/repositories.d/apk-repository.list`

两个 bootstrap 包互相冲突，防止无意中同时启用多个通道；只配置本仓库专用文件，不修改 `customfeeds.list` 或官方 `distfeeds.list`。第一次安装先核对并放置公钥，再安装已签名的 bootstrap。

源使用 `@lauyv` tag。设备通过 `apk add -u sing-box@lauyv luci-app-sing-box@lauyv` 更新指定包。保留官方 feeds 解析依赖，不自动更新整个系统。

## CI 边界

工具链通过 GitHub Actions Cache 缓存编译好的 APK Tools，缓存键包含 runner 系统、CPU 架构、镜像版本、工具配置和构建脚本摘要；仅精确命中时跳过编译，恢复后检查工具版本。运行库仍正常安装。uv 下载缓存按锁文件和 Python 配置失效，由 setup-uv 区分平台与架构。

OpenWrt rootfs 按架构缓存，缓存键包含安装检查脚本摘要（含固定镜像 SHA-256），缓存文件以镜像摘要命名。无论下载还是缓存恢复，都在导入 Docker 前校验 SHA-256。安装检查仅在手动发布勾选 `check_installation` 时执行；定时发布和默认手动发布跳过。启用检查时两架构必须成功，否则禁止部署；跳过检查时仍要求独立完整性校验成功。

原始 APK 下载缓存以 GitHub SHA-256 命名，恢复后重新校验摘要、大小和元数据。签名 job 另行恢复上次经过完整校验的签名站点，用当前私钥导出的公钥认证旧 `SHA256SUMS.sig`，再通过签名覆盖的校验清单认证旧 manifest。仅来源、提交、Release、资产 ID 和摘要、版本、ABI、包配置及处理代码指纹一致的 APK 可复用，逐包重新检查摘要、签名和元数据。旧 manifest 无指纹、缓存损坏或密钥轮换时回退到原始 APK 重新签名。缓存保存最新经过签名和完整校验的候选站点，不要求该候选已经部署；只从中读取本次选中的兼容 APK，不沿用旧索引或清单。缓存不包含私钥，丢失时正常重新构建或下载，Actions artifact 的 1 天保留策略不变。

每日北京时间 04:00 先执行 `uv run --locked python -m apk_repository check-updates`，沿用构建阶段的各通道选择规则，将上游 Release、源码提交、资产 ID 和摘要与线上 `manifest.json` 比较。无变化时跳过后续任务，不请求签名审批；站点返回 404 时执行首次发布，其他网络或解析错误使检查失败。手动触发始终重新发布，适用于配置、代码或密钥变更。检查只读取元数据，构建阶段再次解析并校验上游实际资产。

`assemble` 没有签名密钥；`sign` 只处理和签名包，不安装或执行包脚本；后续 `verify` 和 `check-installation` job 不接触生产私钥。OpenWrt 25.12.0 rootfs 的固定 URL 和 SHA-256 来自官方 [x86/64](https://downloads.openwrt.org/releases/25.12.0/targets/x86/64/) 和 [armsr/armv8](https://downloads.openwrt.org/releases/25.12.0/targets/armsr/armv8/) 镜像列表。启用安装检查时，分别在 `ubuntu-24.04` 和 `ubuntu-24.04-arm` 原生 runner 上执行。修改依赖、bootstrap、固件 ABI 或发布逻辑后，应手动启用安装检查。

容器通过 HTTP 读取实际 feed，完成依赖解析、安装和移除，并运行 `sing-box version`。另外按 APK 版本比较规则，将两个通道中同名包从较低版本安装后升级到较高版本，检查新版本已安装且旧版本不再存在；每个包使用独立容器。只有一个通道或版本相同的包明确记录跳过升级检查，不以重复安装代替跨版本升级，也不为测试保留历史包。容器使用 `--no-scripts`，此检查覆盖依赖解析和文件更新，不覆盖安装钩子、真实路由器的 procd、TUN 和网络功能。
