# -*- coding: utf-8 -*-
"""
Google Patents provider（**keyless / 免注册**）：调用其公开 xhr 查询端点，覆盖中国专利
（含机器翻译）及全球专利，可作为国知局不可用时的稳定回退。

属**非官方**端点，可能限流或变更结构；运行期异常一律吞掉并返回 ``[]``，由编排器回退。
``parse_xhr_json`` 为**纯函数**，便于离线针对样本做固定测试（不依赖网络）。

仅用 Python 标准库（``urllib``），不引入额外依赖。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request

from provider_base import Hit, Provider

_XHR = "https://patents.google.com/xhr/query"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _strip_tags(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip() or None


def _join_codes(val) -> str | None:
    """分类号字段可能为字符串或列表（元素为 str 或 {'code': ...}）；归一为 "; " 连接的字符串。"""
    if not val:
        return None
    items = val if isinstance(val, list) else [val]
    codes: list[str] = []
    for it in items:
        code = it.get("code") if isinstance(it, dict) else it
        if isinstance(code, str) and code.strip():
            codes.append(code.strip())
    # 去重保序
    seen: set[str] = set()
    uniq = [c for c in codes if not (c in seen or seen.add(c))]
    return "; ".join(uniq) or None


def parse_xhr_json(obj: dict) -> list[Hit]:
    """
    解析 Google Patents xhr 响应。其结构为 ``results.cluster[].result[].patent``，
    其中 ``patent`` 含 ``publication_number`` / ``title`` / ``snippet`` 等（可能带高亮标签），
    分类号可能出现在 ``cpc`` / ``ipc``（字符串或列表）——**尽力解析**，缺失则留空。
    结构若变更，仅需调整本函数。
    """
    out: list[Hit] = []
    results = (obj or {}).get("results") or {}
    for cluster in results.get("cluster") or []:
        for item in cluster.get("result") or []:
            pat = item.get("patent") or {}
            num = (pat.get("publication_number") or "").strip() or None
            title = _strip_tags(pat.get("title"))
            snippet = _strip_tags(pat.get("snippet"))
            link = f"https://patents.google.com/patent/{num}/en" if num else None
            if not (num or title):
                continue
            out.append(
                Hit(
                    source="google_patents",
                    title=title,
                    pub_number=num,
                    link=link,
                    abstract=snippet,
                    cpc=_join_codes(pat.get("cpc")),
                    ipc=_join_codes(pat.get("ipc")),
                )
            )
    return out


class GooglePatentsProvider(Provider):
    name = "google_patents"
    quality_rank = 20  # CN 官方源之后的稳定回退；全球覆盖

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def available(self) -> tuple[bool, str]:
        # keyless：始终"可尝试"；真正不可达时 search() 返回 []，由编排器回退。
        return (True, "")

    def search(self, term: str) -> list[Hit]:
        try:
            inner = urllib.parse.urlencode({"q": term})
            url = f"{_XHR}?url={urllib.parse.quote(inner)}&exp="
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": _UA,
                    "Accept": "application/json",
                    "Referer": "https://patents.google.com/",
                },
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
            return parse_xhr_json(json.loads(data))
        except Exception as e:
            # 仍优雅降级返回 []，但留一行 ASCII 诊断：否则端点变更 / 限流 / 解析 bug
            # 与"真的无结果"无法区分（该端点为非官方、schema 未经长期验证）。
            print(
                "PA_SRC: google_patents term_failed error=%s" % repr(str(e))[:200],
                file=sys.stderr,
                flush=True,
            )
            return []
