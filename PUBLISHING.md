# Publishing to PyPI

## Option A: one-time upload with API token (fastest)

1. Create an API token at https://pypi.org/manage/account/token/
2. Run:

```sh
export UV_PUBLISH_TOKEN='pypi-...'
bash scripts/publish-all.sh
```

## Option B: GitHub Actions trusted publishing (recommended for releases)

For each plugin repo, add a **pending publisher** on PyPI:

https://pypi.org/manage/account/publishing/

| Field | Value |
|-------|-------|
| PyPI project name | `takopi-transport-feishu` (or qoder / feishu) |
| Owner | `RicardoKLee` |
| Repository | `takopi-transport-feishu` |
| Workflow | `publish.yml` |
| Environment | *(leave empty)* |

Then trigger:

```sh
gh workflow run publish.yml -R RicardoKLee/takopi-transport-feishu
```

Or create a GitHub Release to publish automatically.
