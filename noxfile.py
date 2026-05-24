"""Nox 多版本测试配置。"""

import nox


@nox.session(python=["3.10", "3.11", "3.12"])
def tests(session: nox.Session) -> None:
    """运行测试套件。"""
    session.install("-e", ".[dev]")
    session.run("pytest", "--cov", *session.posix_args)


@nox.session(python=["3.12"])
def lint(session: nox.Session) -> None:
    """运行代码检查。"""
    session.install("ruff", "mypy")
    session.install("-e", ".")
    session.run("ruff", "check", "src/", "tests/")
    session.run("mypy", "src/")


@nox.session(python=["3.12"])
def docs(session: nox.Session) -> None:
    """构建文档。"""
    session.install("-e", ".[docs]")
    session.run("mkdocs", "build", "--strict")
