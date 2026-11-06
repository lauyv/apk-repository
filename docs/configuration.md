# 配置与构建

Python 3.12+，使用 uv 和标准库，Ruff 为开发依赖。

```sh
uv sync --locked
uv run --locked ruff check .
uv run --locked python -m apk_repository validate
uv run --locked python -m apk_repository plan
uv run --locked python -m unittest discover -s tests -v

# APK Tools 在 Linux CI 中准备；输出目录必须尚不存在
uv run --locked python -m apk_repository build \
  --apk build/tools/apk --output build/unsigned --cache build/cache/upstream-apks
uv run --locked python -m apk_repository sign \
  --apk build/tools/apk --input build/unsigned --output build/public --key /secure/signing.key
uv run --locked python -m apk_repository verify --apk build/tools/apk --site build/public
```

全局 `--root <path>` 放在子命令前。私钥权限须为 0600。构建下载上游 APK，签名阶段追加仓库签名并生成索引，验证通过后才部署。只发布 latest，每个包选择上游发布列表中首个非草稿 Release，包含正式版和预发布版。

## repository.json

| 字段 | 内容 |
|---|---|
| `schema_version`、`name` | `1`、`apk-repository` |
| `base_url` | HTTPS 发布地址，无末尾斜杠 |
| `apk_tools` | APK Tools 的版本、源码地址和完整提交 SHA |
| `packages` | 读取 `packages/<name>/package.json` 的包名列表 |
| `targets` | `arch` 和 `abi`；当前为 x86_64、aarch64_generic，ABI 均为 openwrt-25.12 |

URL 不包含 ABI，同一站点不能混合固件系列。APK Tools 固定为 3.0.5、提交 `b5a31c0d865342ad80be10d68f1bb3d3ad9b0866`。

## package.json

| 字段 | 内容 |
|---|---|
| `schema_version`、`name` | `1`、与目录相同的包名 |
| `source` | GitHub `owner/repository` |
| `architectures` | 各目标架构的 `asset_pattern` 和 `package_arch` |

包名列表中的包全部参与构建。新增包需注册到根配置；移除包则从列表删除。配置不提供禁用状态、多通道或固定包修订号。

`asset_pattern` 含且仅含一个 `{version}` 或 `{apk_version}`。前者去掉标签的 `v` 和 `-rN`，后者转换为 APK 版本格式。例如 `v0.1.0-beta.5-r3` 对应 `0.1.0-beta.5` 和 `0.1.0_beta5`。资产名称带修订号时使用 `{revision}`，否则从 APK 元数据读取。标签带 `-rN` 时要求与包修订号一致。

Honk 同步两个目标架构的 `luci-app-honk-{apk_version}-r{revision}-<架构>.apk`。核心、Geo 数据和依赖声明由上游 APK 提供。

## 缓存与安装检查

`build --cache` 按 GitHub SHA-256 复用下载，重新校验摘要、大小和包元数据。只清理指定目录内 `apk-repository-downloads-v1/` 下本次未使用的摘要文件。每次构建独立站点，不合并旧发布目录，不缓存签名站点。索引只在签名阶段生成。

手动发布的 `check_installation` 默认关闭；启用时在两个架构的原生 Linux runner 检查安装和移除，全部通过才能部署。修改依赖、固件 ABI 或安装逻辑后应启用。新增架构还需更新固定 rootfs、摘要和工作流矩阵；本地测试不能代替远端安装检查。
