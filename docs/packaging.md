# Stock Agent 本地软件打包方案

## 目标

Stock Agent 的正式本地版采用 macOS App 包形式发布：

- 程序文件：`Stock Agent.app`
- 用户数据：`~/Library/Application Support/StockAgent/`
- 日志：`~/Library/Logs/StockAgent/`

程序和用户数据分离后，后续更新只替换 App，不覆盖自选股、持仓、交易记录和历史报告。

## 生成发布包

在项目根目录运行：

```bash
.venv/bin/python scripts/build_release.py --clean
```

输出文件：

- `dist/StockAgent-版本号-macos.zip`：完整安装包
- `dist/StockAgent-版本号-update.zip`：更新包
- `dist/StockAgent-版本号-macos/`：未压缩的安装目录

## 用户安装或更新

用户解压 zip 后双击：

```text
安装或更新.command
```

它会把 App 安装到：

```text
~/Applications/Stock Agent.app
```

如果已有旧版本，会先备份旧 App，再复制新版本。

## 首次启动行为

双击 `Stock Agent.app` 后，启动器会：

1. 创建用户数据目录
2. 首次运行时复制默认 `config/local.json` 和 `data/portfolio.local.json`
3. 首次运行时创建 `~/Library/Application Support/StockAgent/.venv`
4. 安装 `requirements.txt` 中的依赖
5. 启动本地服务 `127.0.0.1:8765`
6. 打开默认浏览器访问 `http://127.0.0.1:8765/#realtime`

## 更新包规则

后续版本继续用同一个脚本生成更新包。更新包可以直接覆盖 App，因为以下文件不会放在 App 内：

- `config/local.json`
- `data/portfolio.local.json`
- `reports/`
- `.venv`

如果未来需要做“增量更新”，可以在 update zip 里只放变更文件和一个替换脚本；但当前 App 包体很小，直接整包更新更稳。

实际迭代流程：

1. 修改代码并验证本地 Web/CLI 行为
2. 提升 `stock_agent/__init__.py` 里的版本号
3. 提交代码仓库
4. 运行 `.venv/bin/python scripts/build_release.py --clean`
5. 使用 `dist/StockAgent-版本号-update.zip` 更新已安装应用

`update.zip` 和完整安装包都会包含同一个最新 App。区别只在使用语义：首次安装发完整安装包，已有用户后续发更新包。

## 版本号

默认版本号来自：

```text
stock_agent/__init__.py
```

也可以手动指定：

```bash
.venv/bin/python scripts/build_release.py --version 0.1.1
```

## 注意

当前包不内置 Python 运行时。首次启动依赖 macOS 自带或用户已安装的 `python3`，并联网安装 AKShare。
如果之后希望完全离线安装，可以再做一个 “runtime bundle” 版本，把 Python 和依赖一起打进 App。
