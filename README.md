# takopi-transport-feishu

Feishu/Lark transport plugin for [Takopi](https://github.com/RicardoKLee/takopi).

## install

```sh
uv pip install "takopi-transport-feishu @ git+https://github.com/RicardoKLee/takopi-transport-feishu.git"
```

or from this checkout:

```sh
uv pip install -e .
```

## usage

```sh
takopi cursor --transport feishu --no-onboard
```

Verify with `takopi plugins --load`.
