# apk-repository

适用于 OpenWrt 25.12 的 `x86_64` 和 `aarch64_generic` APK 软件源，订阅后可通过 `apk` 安装和更新软件包。

软件源发布地址：[lauyv/apk-repository](https://lauyv.github.io/apk-repository)。

首批接入：

| 包                  | 用途          | 上游                      |
| ------------------- | ------------- | ------------------------- |
| `sing-box`          | 代理核心      | `SagerNet/sing-box`       |
| `luci-app-sing-box` | LuCI 管理界面 | `lauyv/luci-app-sing-box` |

软件包来自上游编译好的 OpenWrt APK，保留原始内容和依赖。其他架构、opkg/IPK 系统不适用当前订阅地址。

## 订阅软件源

### 1. 卸载原有软件包

进入 **系统 → 软件包 → 已安装**，卸载之前由其他软件源或手动安装的 `luci-app-sing-box`、`sing-box`。如果还装有依赖它们的扩展包，先处理这些依赖。卸载前按需备份 `/etc/config/sing-box`，并确认页面显示两个包均已移除。

### 2. 配置软件源公钥

LuCI 软件包页面目前没有公钥导入入口。首次订阅时通过 SSH 登录路由器，执行：

```sh
wget -O /tmp/apk-repository.pem 'https://lauyv.github.io/apk-repository/apk/apk-repository.pem'
sha256sum /tmp/apk-repository.pem
```

将输出的 SHA-256 与维护者提供的可信指纹核对，确认一致后执行：

```sh
mkdir -p /etc/apk/keys
mv /tmp/apk-repository.pem /etc/apk/keys/apk-repository.pem
```

### 3. 在页面添加软件源

可在 SSH 中运行 `cat /etc/apk/arch` 确认设备使用的包架构。进入 **系统 → 软件包 → 配置**，打开 `/etc/apk/repositories.d/customfeeds.list`。保留已有内容，另起一行添加下面对应设备架构的地址。

| 设备架构          | 推荐的 latest 源                                                                 |
| ----------------- | -------------------------------------------------------------------------------- |
| `x86_64`          | `https://lauyv.github.io/apk-repository/apk/latest/x86_64/packages.adb`          |
| `aarch64_generic` | `https://lauyv.github.io/apk-repository/apk/latest/aarch64_generic/packages.adb` |

`latest` 对每个包选择上游最近发布的非草稿版本，不区分正式版和预发布版。若只需要正式版，可把地址中的 `latest` 改成 `stable`。同一时间只添加一个本仓库通道，不要同时添加多个地址。

点击 **保存**，关闭配置窗口后点击 **更新列表**。保留官方 `distfeeds.list`，以便解析软件包依赖。

### 4. 在页面安装和更新

在 **系统 → 软件包 → 可用** 中搜索 `sing-box` 和 `luci-app-sing-box`，确认显示的版本与[软件源发布清单](https://lauyv.github.io/apk-repository/manifest.json)中所选通道、架构一致，再分别点击 **安装**。日后在该页面更新列表并更新这两个包即可。若页面列出多个同名包且无法确认所选版本来自本源，请先核对版本和软件源，避免装回其他来源的包。

本源软件包依赖与当前固件匹配的官方内核模块。其他架构及 opkg/IPK 系统不适用上述地址。

切换通道时，在同一配置页面替换原有的本仓库地址，保存并更新列表。停止使用时，删除该行并更新列表；保留其他源和公钥文件。

## 其他说明

订阅或安装问题可在 [Issues](https://github.com/lauyv/apk-repository/issues) 反馈。安全问题见 [SECURITY.md](SECURITY.md)，实现与贡献说明见 [docs](docs/README.md)。

本仓库代码与文档使用 [MIT](LICENSE)，上游软件包保留各自许可证。当前版本的上游源码归档和许可证随站点发布。
