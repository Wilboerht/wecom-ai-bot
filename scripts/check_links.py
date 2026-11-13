"""检查 links.json 的短链目标。

默认只做本地检查（URL 格式、小红书 apptime 年龄）；加 --network 才发 HTTP 请求。
用于定期维护，不参与应用启动，避免网络故障影响服务。

用法:
    python scripts/check_links.py
    python scripts/check_links.py --max-age-days 30
    python scripts/check_links.py --network --strict
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

DEFAULT_LINKS = Path("links.json")


def load_links(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("links.json 顶层必须是 JSON 对象")
    return data


def apptime_age_days(url: str, now: datetime | None = None) -> int | None:
    """小红书分享链接里的 apptime（Unix 秒）；没有该参数返回 None。"""
    query = parse_qs(urlparse(url).query)
    raw = (query.get("apptime") or [""])[0]
    if not raw.isdigit():
        return None
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - datetime.fromtimestamp(int(raw), timezone.utc)).total_seconds() // 86400))


def check_url_format(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "URL scheme 必须是 http/https"
    if not parsed.netloc:
        return "URL 缺少域名"
    return None


def check_http(url: str, timeout: float = 8.0) -> str | None:
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    except Exception as e:
        return f"HTTP 请求失败: {e}"
    if resp.status_code >= 400:
        return f"HTTP {resp.status_code}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查 links.json 短链目标")
    parser.add_argument("--file", default=str(DEFAULT_LINKS))
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--network", action="store_true", help="实际请求目标 URL")
    parser.add_argument("--strict", action="store_true", help="发现过期/失败时返回非 0")
    args = parser.parse_args(argv)

    path = Path(args.file)
    try:
        links = load_links(path)
    except Exception as e:
        print(f"[ERROR] 读取 {path} 失败: {e}", file=sys.stderr)
        return 2

    problems: list[str] = []
    for code, url in links.items():
        if not isinstance(code, str) or not isinstance(url, str):
            problems.append(f"{code!r}: 条目类型错误")
            continue
        fmt_error = check_url_format(url)
        if fmt_error:
            problems.append(f"{code}: {fmt_error}")
            continue
        age = apptime_age_days(url)
        if age is not None and age > args.max_age_days:
            problems.append(f"{code}: 分享参数 apptime 已 {age} 天（阈值 {args.max_age_days}）")
        if code.startswith("oil-") and urlparse(url).netloc in ("nihplod.cn", "www.nihplod.cn"):
            problems.append(f"{code}: 当前是官网占位链接，请替换为真实商品链接")
        if args.network:
            http_error = check_http(url)
            if http_error:
                problems.append(f"{code}: {http_error}")

    if problems:
        for item in problems:
            print(f"[WARN] {item}")
        return 1 if args.strict else 0
    print(f"[OK] {len(links)} 条链接检查通过（network={args.network}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
