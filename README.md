# apk-repository

面向 **OpenWrt 25.12 / x86_64、aarch64_generic** 的 APK 软件源。订阅后可通过 `apk` 安装和更新软件包。

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
- 每日 UTC 04:23（北京时间 12:23）检查上游，版本、源码提交或 APK 资产变化时才发布；无变化则跳过构建和签名。首次发布时站点尚不存在也会触发发布。手动运行工作流可强制重新发布。

## 订阅软件源

可选择下方 LuCI 页面方案或命令行方案，使用其中一种即可。

先通过 `apk --print-arch` 确认设备的包架构，按下表选择订阅地址。其他 ARM64 架构不能仅凭 CPU 为 ARM64 就使用 `aarch64_generic` 源。

| 架构 | 稳定源 |
|---|---|
| `x86_64` | `https://lauyv.github.io/apk-repository/apk/stable/x86_64/packages.adb` |
| `aarch64_generic` | `https://lauyv.github.io/apk-repository/apk/stable/aarch64_generic/packages.adb` |

预发布源将地址中的 `stable` 替换为 `prerelease`。

### OpenWrt 页面配置（LuCI）

进入 **系统 → 软件包（Software）→ 配置（Configure）**，可编辑 APK 软件源；标准软件包页面不提供公钥导入，首次仍需通过 SSH 放置公钥。以下页面示例使用 `x86_64`，`aarch64_generic` 设备请替换地址中的架构部分。

1. 通过 SSH 登录路由器，下载公钥并查看指纹：

   ```sh
   wget -O /tmp/apk-repository.pem 'https://lauyv.github.io/apk-repository/apk/apk-repository.pem'
   sha256sum /tmp/apk-repository.pem
   ```

   与维护者提供的可信指纹核对一致后执行：

   ```sh
   mkdir -p /etc/apk/keys
   cp /tmp/apk-repository.pem /etc/apk/keys/apk-repository.pem
   ```

2. 在页面的配置窗口中找到 **`/etc/apk/repositories.d/customfeeds.list`**，保留已有内容，另起一行添加稳定源：

   ```text
   @lauyv https://lauyv.github.io/apk-repository/apk/stable/x86_64/packages.adb
   ```

   如果需要预发布版，使用下面这一行代替，只保留一个通道：

   ```text
   @lauyv https://lauyv.github.io/apk-repository/apk/prerelease/x86_64/packages.adb
   ```

3. 点击 **保存（Save）**，关闭配置窗口后点击 **更新列表（Update lists）**。保留官方 `distfeeds.list`，供本仓库的软件包解析依赖。

4. 通过 SSH 明确选择本仓库安装或更新：

   ```sh
   apk add -u sing-box@lauyv luci-app-sing-box@lauyv
   ```

   页面普通安装按钮不显式指定 `@lauyv`，同名包可能也存在于官方源，因此使用上述命令选择来源。安装完成后可在页面查看已安装的软件包。

页面和命令行方案均使用 `customfeeds.list`，配置后可直接在 LuCI 中查看和编辑；站点配置包仍使用单独的 `apk-repository.list`，不要重复添加。LuCI 的配置文件入口可参考 [OpenWrt 官方实现](https://github.com/openwrt/luci/blob/master/applications/luci-app-package-manager/htdocs/luci-static/resources/view/package-manager.js)。

### 命令行配置

在路由器上以 root 执行。下载公钥并查看指纹：

```sh
wget -O /tmp/apk-repository.pem 'https://lauyv.github.io/apk-repository/apk/apk-repository.pem'
sha256sum /tmp/apk-repository.pem
```

与维护者通过可信渠道提供的公钥指纹核对一致后，订阅稳定版：

```sh
(
  set -eu
  arch=$(apk --print-arch)
  case "$arch" in
    x86_64|aarch64_generic) ;;
    *) echo "Unsupported architecture: $arch" >&2; exit 1 ;;
  esac
  mkdir -p /etc/apk/keys /etc/apk/repositories.d
  cp /tmp/apk-repository.pem /etc/apk/keys/apk-repository.pem
  touch /etc/apk/repositories.d/customfeeds.list
  cp -p /etc/apk/repositories.d/customfeeds.list /etc/apk/repositories.d/customfeeds.list.bak
  sed -i '/^[[:space:]]*@lauyv[[:space:]]/d' /etc/apk/repositories.d/customfeeds.list
  printf '\n%s\n' "@lauyv https://lauyv.github.io/apk-repository/apk/stable/$arch/packages.adb" \
    >> /etc/apk/repositories.d/customfeeds.list
  apk update
  apk add -u sing-box@lauyv luci-app-sing-box@lauyv
)
```

命令先备份 `customfeeds.list`，再替换其中带 `@lauyv` 标签的订阅行，保留其他自定义源及官方 feeds。重复执行不会重复添加本仓库地址。源中的 `@lauyv` 用于明确选用本仓库的软件包。

sing-box 上游包会依赖内核模块，因此设备的官方软件源必须与当前固件内核匹配。本仓库不提供替换固件内核或修改这些依赖的包。

## 更新与切换通道

```sh
apk update
apk add -u sing-box@lauyv luci-app-sing-box@lauyv
```

手动订阅的设备选择预发布版时，将 `customfeeds.list` 中本仓库地址的 `stable` 改为 `prerelease`，再执行上述命令，只启用一个通道。使用配置包的设备，先完整执行下方“移除订阅”，再按站点 `install.txt` 安装新通道配置包和软件包，重新添加仓库标签。切回稳定源不会自动降级已安装的较新预发布包。不执行全系统 `apk upgrade`。

通过 LuCI 配置的设备，在 **系统 → 软件包 → 配置** 中修改 `customfeeds.list` 内本仓库地址，保存并更新列表，再执行上述指定包更新命令。

## 移除订阅

手动订阅和使用配置包的设备均按以下顺序操作：备份 `/etc/apk/world`，清理其中的 `@lauyv` 标签，再移除配置包和本仓库专用文件。保留软件包条目、版本约束及其他仓库标签，避免删除源后 APK 因缺失标签而拒绝后续操作。

```sh
(
  set -eu
  cp -p /etc/apk/world /etc/apk/world.before-apk-repository-removal
  sed -i -E 's/@lauyv([<>=~]|$)/\1/g' /etc/apk/world
  for package in apk-repository-stable apk-repository-prerelease; do
    if apk info -e "$package" >/dev/null 2>&1; then
      apk del "$package"
    fi
  done
  if [ -f /etc/apk/repositories.d/customfeeds.list ]; then
    cp -p /etc/apk/repositories.d/customfeeds.list /etc/apk/repositories.d/customfeeds.list.bak
    sed -i '/^[[:space:]]*@lauyv[[:space:]]/d' /etc/apk/repositories.d/customfeeds.list
  fi
  rm -f /etc/apk/repositories.d/apk-repository.list
  rm -f /etc/apk/keys/apk-repository.pem
)
```

这会保留已安装的应用包，并停止通过本仓库更新；后续 APK 操作会按剩余软件源和 world 约束解析版本。其他软件源与公钥保持不变。不要只删除源文件或只卸载配置包，否则 world 中可能留下失效标签。

上述命令同时适用于命令行和 LuCI 配置的订阅，只清理 `customfeeds.list` 中带 `@lauyv` 标签的行，保留其他软件源。执行成功后可在 LuCI 中确认配置并更新列表，不能删除整个 `customfeeds.list`。

站点的 `install.txt` 也提供当前配置包的安装命令。站点返回 404 时表示当前无法订阅，请等待维护者完成发布；不要跳过签名校验。

## 其他说明

订阅或安装问题可在 [Issues](https://github.com/lauyv/apk-repository/issues) 反馈。安全问题见 [SECURITY.md](SECURITY.md)，实现与贡献说明见 [docs](docs/README.md)。

本仓库代码与文档使用 [MIT](LICENSE)，上游软件包保留各自许可证。当前版本的上游源码归档和许可证随站点发布。
