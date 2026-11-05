# 配置与扩展

项目使用 Python 3.12+、uv 和标准库，无第三方 Python 运行时依赖。`.python-version`、`pyproject.toml` 和 `uv.lock` 一起提交。

## 命令

以下构建和验证命令已接入 GitHub Actions；无需在开发机器安装 APK Tools 或 Docker。

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked python -m apk_repository validate
uv run --locked python -m apk_repository plan --channel latest
uv run --locked python -m unittest discover -s tests -v

# 需要固定版本的 APK Tools；输出目录必须尚不存在
uv run --locked python -m apk_repository build --apk build/tools/apk --output build/unsigned --cache build/cache/upstream-apks

# 只为 bootstrap 构造新包；上游 APK 仅追加签名
fakeroot uv run --locked python -m apk_repository sign \
  --apk build/tools/apk --input build/unsigned --output build/public --key /secure/signing.key \
  --previous build/cache/signed
uv run --locked python -m apk_repository verify --apk build/tools/apk --site build/public
```

命令失败返回非零退出码。`plan` 只展示计划，`build` 下载真实资产但不使用生产密钥，`sign` 生成完整站点，`verify` 检查站点完整性和签名。`--root <path>` 是子命令之前的全局选项。

`build --cache` 可选，在指定目录的专属子目录 `apk-repository-downloads-v1/` 中按上游 SHA-256 复用原始 APK，仍检查摘要、大小和包元数据；只清理该子目录内不再使用的 SHA-256 文件，不清理指定目录中的其他文件。`sign --previous` 可选，读取上次经过验证的签名站点，认证清单并逐包校验兼容性、摘要和当前公钥签名后复用 APK；先复制到临时文件并校验，成功后原子替换，复制或校验失败时保留原始 APK。旧索引、bootstrap 和站点清单仍重新生成。目录不存在、旧版清单不支持复用、缓存损坏或密钥变化时正常重新处理。

GitHub Actions 手动发布的 `check_installation` 选项默认关闭；勾选后运行两个架构的安装和升级检查，全部通过才能部署。定时发布跳过安装检查，始终保留完整性校验。修改依赖、bootstrap、固件 ABI 或发布逻辑后，应手动勾选此选项进行验证。

## repository.json

| 字段 | 说明 |
|---|---|
| `schema_version` | 当前为整数 `1` |
| `name` | `apk-repository` |
| `base_url` | HTTPS 站点地址，不含末尾斜杠 |
| `channels` | `stable`、`latest` |
| `apk_tools` | 固定源码地址、完整提交 SHA 和版本 |
| `packages` | 已注册包名，读取 `packages/<name>/package.json` |
| `targets` | 精确 OpenWrt 包架构和 ABI；当前为 `x86_64`、`aarch64_generic`，ABI 均为 `openwrt-25.12` |

当前路径不包含 ABI，禁止混放不同 ABI。扩展其他固件系列时应先扩展 URL 和相应安装检查，不能直接修改现有 feed 的含义。

APK Tools 使用 `3.0.5`、提交 `b5a31c0d865342ad80be10d68f1bb3d3ad9b0866`，来自 [OpenWrt 的 APK 配置](https://github.com/openwrt/openwrt/blob/main/package/system/apk/Makefile)。Actions 通过 GitHub 上的 Alpine 官方镜像下载同一提交并编译完整工具，避免系统自带 APK v2 或缺失 `mkndx`/`adbsign`。

## package.json

| 字段 | 说明 |
|---|---|
| `schema_version`、`name` | 格式版本为 `1`；包名与注册清单及目录一致 |
| `enabled` | 是否参与同步与发布 |
| `source` | 固定 GitHub `owner/repository` |
| `method` | 当前只接受 `sync-apk` |
| `channels` | 参与的通道 |
| `revision` | 固定的上游 APK `-rN`；设为 `null` 时，从资产文件名或下载后 APK 元数据读取修订号，不是本仓库重新打包次数 |
| `source_assets` | 可选的 Release 源码资产模式列表；支持 `{tag}` 和 `*`，每个模式必须唯一匹配一个 `.tar.gz` 资产，校验 GitHub 摘要并随站点发布 |
| `architectures` | 精确架构到 `asset_pattern`、`package_arch` 的映射 |

版本示例：Release `v1.14.0-beta.8` 对应上游资产版本 `1.14.0-beta.8`，APK 版本为 `1.14.0_beta8-r0`。只接受数字版本和 alpha/beta/rc 后缀，不猜测未知版本格式。

`asset_pattern` 必须含且仅含一个 `{version}` 或 `{apk_version}`；前者为去掉 `v` 的上游版本，后者为 APK 版本（不含 `-rN`，例如 `1.14.0_beta8`），根据实际 Release API 资产列表匹配；不通过模板构造下载地址。`revision` 为 `null` 且资产名带 `-rN` 时，可使用 `{revision}`，只接受唯一匹配的非负整数修订号；资产名不带修订号时，从 APK 元数据读取，并要求版本与 Release 标签一致。所有包均校验包内完整版本。sing-box 只选 `openwrt` 命名的资产。架构无关的 LuCI 包可为 `noarch`，索引仍按设备架构分别发布。

`stable` 使用 GitHub 的 latest Release API 选择正式版。`latest` 遍历完整 Release 列表，按发布时间选择最新非草稿记录，不区分正式版和预发布版。API 分页读取，摘要缺失、元数据变化、资产缺失均阻止发布。

sing-box 的 `-r0` 和 OpenWrt 架构名依据[上游 APK 打包脚本](https://github.com/SagerNet/sing-box/blob/v1.14.1/.github/build_openwrt_apk.sh)登记，流水线还会对每次实际下载的包重新检查。

`luci-app-honk` 仅配置 `latest`，发布标签跟随内置 Honk 核心版本。资产模式为 `luci-app-honk-{apk_version}-r{revision}-<架构>.apk`，分别同步 `x86_64` 和 `aarch64_generic`，并要求包内架构与目标一致。例如 `v0.1.0-beta.4` 对应 `luci-app-honk-0.1.0_beta4-r1-x86_64.apk`。通过 `source_assets` 同步同一 Release 的 `honk-source-{tag}.tar.gz` 和唯一的 `doona-source-*.tar.gz`；两者及 LuCI 源码均提供下载链接，并纳入站点校验和与签名。源码资产缺失、匹配不唯一、摘要或来源变化均阻止签名；更新检测也比较源码资产 ID、名称和摘要。需要接入正式版通道时，可将 `stable` 加入该包的 `channels`。

## 新增包或架构

新增包时添加独立 `package.json` 并注册到根配置即可复用同步、签名和索引流程。配置只声明来源、版本规则和架构。流水线校验下载摘要、APK v3 数据、包名、版本、架构及签名，原样保留上游包并追加仓库签名。不比对依赖、冲突、许可证、自动安装、替换声明或文件属主；安装时由设备上的 APK 解析依赖。源码归档随发布保留，不单独提取或审核许可证。

新增架构需要添加 target 和各包的精确资产映射。`aarch64_generic` 同步上游 `sing-box_{version}_openwrt_aarch64_generic.apk`，架构无关的 LuCI 包复用 `noarch` APK，内置核心的 `luci-app-honk` 使用对应架构 APK；两架构均执行包元数据、签名和索引校验。`scripts/check-openwrt.sh <site> <arch>` 在对应原生 runner 上检查容器安装和跨版本升级；勾选安装检查时，发布须等待两架构检查完成。新增架构还需增加固定 rootfs、摘要和工作流矩阵，并手动启用安装检查；实际验证结果以远端 Actions 为准。
