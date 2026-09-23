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
uv run --locked python -m apk_repository build --apk build/tools/apk --output build/unsigned

# 只为 bootstrap 构造新包；上游 APK 仅追加签名
fakeroot uv run --locked python -m apk_repository sign \
  --apk build/tools/apk --input build/unsigned --output build/public --key /secure/signing.key
uv run --locked python -m apk_repository verify --apk build/tools/apk --site build/public
```

命令失败返回非零退出码。`plan` 只展示计划，`build` 下载真实资产但不使用生产密钥，`sign` 生成完整站点，`verify` 检查站点完整性和签名。`--root <path>` 是子命令之前的全局选项。

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
| `license`、`license_reviewed` | 上游许可证标识和接入审核声明；源码及 LICENSE 随发布保留 |
| `revision` | 预期上游 APK 的 `-rN`，不是本仓库重新打包次数 |
| `dependencies`、`conflicts` | 预期 APK 元数据，逐项精确校验 |
| `architectures` | 精确架构到 `asset_pattern`、`package_arch` 的映射 |

版本示例：Release `v1.14.0-beta.8` 对应上游资产版本 `1.14.0-beta.8`，APK 版本为 `1.14.0_beta8-r0`。只接受数字版本和 alpha/beta/rc 后缀，不猜测未知版本格式。

`asset_pattern` 必须含 `{version}`，根据实际 Release API 资产列表精确匹配；不通过模板构造下载地址。sing-box 只选 `openwrt` 命名的资产。架构无关的 LuCI 包可为 `noarch`，索引仍按设备架构分别发布。

`stable` 使用 GitHub 的 latest Release API 选择正式版。`latest` 遍历完整 Release 列表，按发布时间选择最新非草稿记录，不区分正式版和预发布版。API 分页读取，摘要缺失、元数据变化、资产缺失均阻止发布。

sing-box 的 `-r0`、OpenWrt 架构名和依赖声明依据[上游 APK 打包脚本](https://github.com/SagerNet/sing-box/blob/v1.14.1/.github/build_openwrt_apk.sh)登记，流水线还会对每次实际下载的包重新检查。

## 新增包或架构

新增包时添加独立 `package.json` 并注册到根配置即可复用同步、签名和索引流程。先确认 APK v3 格式、来源、版本规则、许可证和精确依赖；`build` 会重新核对这些声明。上游有不同元数据时应审查后更新配置，不在流水线中自动放宽。

新增架构需要添加 target 和各包的精确资产映射。`aarch64_generic` 同步上游 `sing-box_{version}_openwrt_aarch64_generic.apk`，LuCI 复用 `noarch` APK；两架构均执行包元数据、签名和索引校验。`scripts/check-openwrt.sh <site> <arch>` 在对应原生 runner 上检查容器安装和跨版本升级，发布须等待两架构检查完成。新增架构还需增加固定 rootfs、摘要和工作流矩阵；实际验证结果以远端 Actions 为准。
