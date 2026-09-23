# apk-repository

面向 **OpenWrt 25.12 / x86_64** 的 APK 软件源。订阅后可通过 `apk` 安装和更新软件包。

代码仓库：[lauyv/apk-repository](https://github.com/lauyv/apk-repository)。软件源发布地址：`https://lauyv.github.io/apk-repository/`。

首批接入：

| 包 | 用途 | 上游 |
|---|---|---|
| `sing-box` | 代理核心 | `SagerNet/sing-box` |
| `luci-app-sing-box` | LuCI 管理界面 | `lauyv/luci-app-sing-box` |

软件包来自上游编译好的 OpenWrt APK，保留原始内容和依赖。其他架构、opkg/IPK 系统不适用当前订阅地址。

## 发布策略

- `stable`：各包最新正式 Release。
- `prerelease`：各包最新 Pre-release；没有预发布版的 LuCI 包可复用最新正式版。
- 每个通道、架构、包名只保留一个版本。每次从空目录构建完整站点，旧 APK、旧源码和旧清单不带入下一次发布。
- 下载或检查失败则整次发布失败，线上站点维持原状。

## 订阅软件源

在路由器上以 root 执行。下载公钥并查看指纹：

```sh
wget -O /tmp/apk-repository.pem 'https://lauyv.github.io/apk-repository/apk/apk-repository.pem'
sha256sum /tmp/apk-repository.pem
```

与维护者通过可信渠道提供的公钥指纹核对一致后，订阅稳定版：

```sh
mkdir -p /etc/apk/keys /etc/apk/repositories.d
cp /tmp/apk-repository.pem /etc/apk/keys/apk-repository.pem
printf '%s\n' '@apk_repository https://lauyv.github.io/apk-repository/apk/stable/x86_64/packages.adb' \
  > /etc/apk/repositories.d/apk-repository.list
apk update
apk add -u sing-box@apk_repository luci-app-sing-box@apk_repository
```

`apk-repository.list` 是本仓库专用文件，保留设备已有的官方 feeds。源中的 `@apk_repository` 用于明确选用本仓库的软件包。

sing-box 上游包会依赖内核模块，因此设备的官方软件源必须与当前固件内核匹配。本仓库不提供替换固件内核或修改这些依赖的包。

## 更新与切换通道

```sh
apk update
apk add -u sing-box@apk_repository luci-app-sing-box@apk_repository
```

手动订阅的设备选择预发布版时，将专用配置文件地址中的 `stable` 改为 `prerelease`，再执行上述命令，只启用一个通道。使用配置包的设备，先完整执行下方“移除订阅”，再按站点 `install.txt` 安装新通道配置包和软件包，重新添加仓库标签。切回稳定源不会自动降级已安装的较新预发布包。不执行全系统 `apk upgrade`。

## 移除订阅

手动订阅和使用配置包的设备均按以下顺序操作：备份 `/etc/apk/world`，清理其中的 `@apk_repository` 标签，再移除配置包和本仓库专用文件。保留软件包条目、版本约束及其他仓库标签，避免删除源后 APK 因缺失标签而拒绝后续操作。

```sh
(
  set -eu
  cp -p /etc/apk/world /etc/apk/world.before-apk-repository-removal
  sed -i -E 's/@apk_repository([<>=~]|$)/\1/g' /etc/apk/world
  for package in apk-repository-stable apk-repository-prerelease; do
    if apk info -e "$package" >/dev/null 2>&1; then
      apk del "$package"
    fi
  done
  rm -f /etc/apk/repositories.d/apk-repository.list
  rm -f /etc/apk/keys/apk-repository.pem
)
```

这会保留已安装的应用包，并停止通过本仓库更新；后续 APK 操作会按剩余软件源和 world 约束解析版本。其他软件源与公钥保持不变。不要只删除源文件或只卸载配置包，否则 world 中可能留下失效标签。

站点的 `install.txt` 也提供当前配置包的安装命令。站点返回 404 时表示当前无法订阅，请等待维护者完成发布；不要跳过签名校验。

## 其他说明

订阅或安装问题可在 [Issues](https://github.com/lauyv/apk-repository/issues) 反馈。安全问题见 [SECURITY.md](SECURITY.md)，实现与贡献说明见 [docs](docs/README.md)。

本仓库代码与文档使用 [MIT](LICENSE)，上游软件包保留各自许可证。当前版本的上游源码归档和许可证随站点发布。
