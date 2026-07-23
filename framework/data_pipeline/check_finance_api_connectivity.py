"""Low-frequency connectivity checks for public finance APIs.

This script intentionally avoids paid APIs and secret-backed services.
It fetches a tiny fixed sample only, so it is safe to run during setup.
"""

from __future__ import annotations

import socket


def check_akshare() -> tuple[bool, str]:
    import akshare as ak

    df = ak.stock_zh_a_hist(
        symbol="000001",
        period="daily",
        start_date="20240102",
        end_date="20240103",
        adjust="",
    )
    return len(df) > 0, f"rows={len(df)}, columns={list(df.columns)[:5]}"


def check_baostock() -> tuple[bool, str]:
    import baostock as bs

    login = bs.login()
    if login.error_code != "0":
        return False, f"login_error={login.error_code}, message={login.error_msg}"

    try:
        rs = bs.query_history_k_data_plus(
            "sh.600000",
            "date,code,open,close",
            start_date="2024-01-02",
            end_date="2024-01-03",
            frequency="d",
            adjustflag="3",
        )
        rows = []
        while rs.next() and len(rows) < 5:
            rows.append(rs.get_row_data())
        if rs.error_code != "0":
            return False, f"query_error={rs.error_code}, message={rs.error_msg}"
        return len(rows) > 0, f"rows={len(rows)}"
    finally:
        bs.logout()


def main() -> int:
    socket.setdefaulttimeout(20)
    checks = [
        ("akshare", check_akshare),
        ("baostock", check_baostock),
    ]
    failed = False
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - setup diagnostic output
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        print(f"{name}: {'ok' if ok else 'failed'}; {detail}")
        failed = failed or not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
