# pytest-lens

[![ci](https://github.com/jszbuzhidao/pytest-lens/actions/workflows/ci.yml/badge.svg)](https://github.com/jszbuzhidao/pytest-lens/actions/workflows/ci.yml)

把散落在每个 CI 构建里的 pytest 结果，汇聚成可追溯的趋势与失败聚类。

`pytest-lens` 解决三个测试工程里的实际痛点：

1. **同一条用例反复红，但每次报错文本都不一样** —— 用*失败指纹*把"同一个缺陷"的多个实例收敛成一条。
2. **不知道哪些用例是 flaky** —— 用历史通过率判定，而不是靠人肉记忆。
3. **CI 跑完只看最后一行 `3 failed`** —— 落库后能看趋势、看最慢用例、看报告。

## 安装

```bash
pip install -e ".[dev]"
```

## 快速开始

```bash
# 1. 灌一份演示数据（内置一个"电商"示例项目，含 flaky 与稳定失败用例）
lens seed-demo

# 2. 看聚合统计
lens stats

# 3. 生成 Markdown 报告
lens report --out report.md

# 4. 打开网页看板
lens serve            # http://127.0.0.1:8000
```

在真实项目里跑测试时，加一个 `--lens` 即可自动落库：

```bash
pytest --lens --lens-project my-service
```

## 导入已有 CI 的 JUnit XML

```bash
lens import junit.xml --project my-service --commit "$GITHUB_SHA"
```

## 命令一览

| 命令 | 作用 |
| --- | --- |
| `lens import <xml>` | 导入 JUnit XML 到一个项目 |
| `lens stats` | 终端打印汇总、趋势、最慢用例、失败聚类 |
| `lens report` | 输出 Markdown 报告 |
| `lens serve` | 启动 Web 看板（FastAPI） |
| `lens seed-demo` | 生成演示数据 |

## 模块结构

```
lens/
├── models.py       CaseResult / RunInfo：用例与一次运行的数据模型
├── fingerprint.py  失败文本归一化 → 稳定 SHA1 指纹
├── store.py        SQLite 存储与查询（趋势 / 聚类 / flaky）
├── flaky.py        由历史结果序列判定 flaky / 稳定失败
├── plugin.py       pytest 插件（pytest11 入口）
├── junitxml.py     JUnit XML 导入
├── report.py       Markdown 报告
├── api.py          FastAPI 接口
├── cli.py          命令行入口
└── web/index.html  单页看板
```

## 失败指纹是怎么算的

同一条用例失败两次，报错里往往只有变量名和地址不同：

```
AssertionError: expected 3, got 7   /tmp/pytest-1/test_x0.py:12
AssertionError: expected 5, got 9   /tmp/pytest-9/test_x1.py:31
```

`fingerprint.py` 会先做归一化——抹掉对象地址（`<object>`）、十六进制（`0xADDR`）、
临时目录（`<tmp>`）、UUID、时间戳、字符串字面量与数字（`N`），再折叠空白——
然后对「异常类型 + 出错文件 + 消息骨架」取 SHA1 前 12 位。

于是上面两条会落到同一个指纹上，看板里表现为"一个缺陷，两次触发"。

## 测试

```bash
pytest -q
coverage run -m pytest && coverage report -m   # 208 个用例，99%（802 条语句）
```

> 为什么不用 `pytest --cov=lens`？本项目通过 `pytest11` 入口注册插件，pytest 会在
> pytest-cov 开始计量**之前**就 `import lens`，于是整个包的模块级代码都被算成「未执行」，
> 覆盖率会虚低到 83%（同一份代码、同一批用例）。要准确数字就用 `coverage run -m pytest`。
>
> Windows 上跑本项目时**不要**加 `--basetemp`，会让 fixture 目录解析异常。

## License

MIT
