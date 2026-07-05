# 贡献与多机开发说明

这个仓库用于多台电脑共同维护 `super-imyaigc-signin` Codex Skill。默认所有协作都走私有 GitHub 仓库。

## 开发流程

1. 开始前同步远端：

```powershell
git pull
```

2. 修改脚本、文档或模板配置。

3. 至少运行一项验证：

```powershell
python ".\scripts\signin.py" --phone YOUR_PHONE --model-count
python ".\scripts\imyai_chat.py" --phone YOUR_PHONE --model "Qwen 3.6 flash" --prompt "Reply exactly: ok" --no-official-history --json
```

4. 提交并推送：

```powershell
git status
git add --all
git commit -m "简短描述本次修改"
git push
```

## 文件同步规则

会同步：

- `SKILL.md`
- `README.md`
- `INSTALL.md`
- `CONTRIBUTING.md`
- `requirements.txt`
- `agents/*.yaml`
- `scripts/*.py`
- `scripts/config.template.json`

不会同步：

- `scripts/config.json`
- `scripts/.secret_key`
- `scripts/sessions/*.json`
- `.local/`
- `__pycache__/`
- `*.pyc`
- `.pytest_cache/`
- `.mypy_cache/`
- `.ruff_cache/`

## 修改脚本时的约定

- 保持命令行参数向后兼容。
- 新增模型选择逻辑时，优先支持人类可读模型名，不要求用户输入内部 ID。
- 新增网络逻辑时，保持顺序清晰：配置代理、直连、环境代理、本地代理检测。
- 新增图片生成参数时，优先通过 `--overrides-json` 或显式 CLI 参数暴露。
- 出现 401 时，优先走 `signin.py --login-only` 刷新登录。
- 不要把本地账号配置、Cookie、session、截图、日志重新提交进仓库。

## 文档约定

- 文档默认使用中文。
- README 中保留 Mermaid 图，方便在 GitHub 页面直接查看流程。
- 新增常用命令时，同时补充用途说明和最小可复制命令。
- 示例手机号统一使用 `YOUR_PHONE`、`SECOND_PHONE`、`THIRD_PHONE` 这类占位符。
