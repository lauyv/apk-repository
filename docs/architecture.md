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

上图以 x86_64 为例；stable 和 prerelease 下同时生成 `aarch64_generic/`，包含该架构的 sing-box、共享的 noarch LuCI 包、独立 bootstrap 和签名索引。两架构均参与更新检查、发布完整性校验和容器安装检查。

## Bootstrap 与设备配置

各通道的 bootstrap APK 安装两个软件源配置文件，并附带自身 MIT 许可证：

- `/etc/apk/keys/apk-repository.pem`
- `/etc/apk/repositories.d/apk-repository.list`

两个 bootstrap 包互相冲突，防止无意中同时启用两个通道；只配置本仓库专用文件，不修改 `customfeeds.list` 或官方 `distfeeds.list`。第一次安装先核对并放置公钥，再安装已签名的 bootstrap。

源使用 `@lauyv` tag。设备通过 `apk add -u sing-box@lauyv luci-app-sing-box@lauyv` 更新指定包。保留官方 feeds 解析依赖，不自动更新整个系统。

## CI 边界

工具链通过 GitHub Actions Cache 缓存编译好的 APK Tools，缓存键包含 runner 系统、CPU 架构、镜像版本、工具配置和构建脚本摘要；仅精确命中时跳过编译，恢复后检查工具版本。运行库仍正常安装。uv 下载缓存按锁文件和 Python 配置失效，由 setup-uv 区分平台与架构。

OpenWrt rootfs 按架构缓存，缓存键包含安装检查脚本摘要（含固定镜像 SHA-256），缓存文件以镜像摘要命名。无论下载还是缓存恢复，都在导入 Docker 前校验 SHA-256。缓存仅包含工具和依赖，不包含私钥或发布站点；站点仍从空目录生成。缓存丢失时正常重新构建或下载，Actions artifact 的 1 天保留策略不变。

每日 UTC 04:23 先执行 `uv run --locked python -m apk_repository check-updates`，沿用构建阶段的 stable/prerelease 选择规则，将上游 Release、源码提交、资产 ID 和摘要与线上 `manifest.json` 比较。无变化时跳过后续任务，不请求签名审批；站点返回 404 时执行首次发布，其他网络或解析错误使检查失败。手动触发始终重新发布，适用于配置、代码或密钥变更。检查只读取元数据，构建阶段再次解析并校验上游实际资产。

`assemble` 没有签名密钥；`sign` 只处理和签名包，不安装或执行包脚本；后续 `verify` 和 `check-installation` job 不接触生产私钥。OpenWrt 25.12.0 rootfs 的固定 URL 和 SHA-256 来自官方 [x86/64](https://downloads.openwrt.org/releases/25.12.0/targets/x86/64/) 和 [armsr/armv8](https://downloads.openwrt.org/releases/25.12.0/targets/armsr/armv8/) 镜像列表。安装检查分别在 `ubuntu-24.04` 和 `ubuntu-24.04-arm` 原生 runner 上执行；完整性检查和两架构安装检查都通过后才能部署。

容器通过 HTTP 读取实际 feed，完成依赖解析、安装和移除，并运行 `sing-box version`。另外按 APK 版本比较规则，将两个通道中同名包从较低版本安装后升级到较高版本，检查新版本已安装且旧版本不再存在；每个包使用独立容器。只有一个通道或版本相同的包明确记录跳过升级检查，不以重复安装代替跨版本升级，也不为测试保留历史包。容器使用 `--no-scripts`，此检查覆盖依赖解析和文件更新，不覆盖安装钩子、真实路由器的 procd、TUN 和网络功能。
